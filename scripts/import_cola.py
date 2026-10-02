"""Turn a folder of TTB Public COLA Registry downloads into the bundled test set.

Input: one or more folders (or zips) as produced by a registry fetch: ``<ttbid>.json``
with the registry metadata, ``<ttbid>_front.*``, ``<ttbid>_back.*``, ``<ttbid>_other<n>.*``
images. Output: ``labelverify/testdata/cola/`` with every image resized for the reader
and re-encoded as JPEG (the registry's PNGs are large), one ``records.json`` with the
registry metadata, ``applications.csv`` in the batch upload's column format, and a
README that states the provenance.

    .venv/bin/python scripts/import_cola.py cola_part1.zip cola_part2.zip

``applications.csv`` starts from the registry's values and then applies
``scenarios.json``: hand-transcribed or deliberately altered application values for a
subset of the COLAs, so one batch shows correct filings, wrong values, near misses and
blank fields side by side. Rewrite only the CSV (after editing the scenarios) with

    .venv/bin/python scripts/import_cola.py --csv-only
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "labelverify" / "testdata" / "cola"
MAX_EDGE = 1400
PANEL_ORDER = {"front": 0, "back": 1}
COLUMNS = [
    "image",
    "beverage_type",
    "brand_name",
    "class_type",
    "alcohol_content",
    "net_contents",
    "producer_name",
    "producer_address",
    "is_import",
    "country_of_origin",
    "scenario",
    "expected",
    "ttbid",
    "registry_class",
    "note",
]
SCENARIOS = OUT / "scenarios.json"
PANEL_STEMS = ("front", "back", "other1", "other2")
REGISTRY_NOTE = (
    "registry metadata; alcohol content and net contents are not on the public record "
    "and are left blank"
)
SCENARIO_NOTES = {
    "filed-correctly": "application values transcribed by hand from the label image",
    "blank-fields": "application fields left blank on purpose",
}
US_NAMES = {"united states", "usa", "us", "u.s.a.", "united states of america"}


def collect(inputs: list[str], workdir: Path) -> list[Path]:
    """Every file from the given zips and folders, extracted into ``workdir``."""
    files: list[Path] = []
    for item in inputs:
        path = Path(item)
        if path.suffix.lower() == ".zip":
            target = workdir / path.stem
            with zipfile.ZipFile(path) as archive:
                archive.extractall(target)
            files.extend(p for p in target.rglob("*") if p.is_file())
        else:
            files.extend(p for p in path.rglob("*") if p.is_file())
    return files


def flatten(image: Image.Image) -> Image.Image:
    """RGB on white; registry PNGs often carry transparency."""
    image = ImageOps.exif_transpose(image) or image
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return image.convert("RGB")


def store_image(src: Path, name: str) -> int:
    image = flatten(Image.open(src))
    if max(image.size) > MAX_EDGE:
        scale = MAX_EDGE / max(image.size)
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS
        )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=84, optimize=True)
    (OUT / "images" / name).write_bytes(buffer.getvalue())
    return len(buffer.getvalue())


def split_applicant(applicant: str) -> tuple[str, str]:
    """The registry's applicant line is "<name parts>, <street>, <city ST zip>": the
    name is everything before the first segment that starts with a number."""
    parts = [p.strip() for p in applicant.split(",") if p.strip()]
    for i, part in enumerate(parts):
        if re.match(r"^\d", part):
            name = ", ".join(parts[:i])
            # "Leiper's Fork Distillery, LLC, Leiper's Fork Distillery, LLC": the trade
            # name and the legal name are the same; list it once.
            half = name[: len(name) // 2].rstrip(", ")
            if len(parts[:i]) % 2 == 0 and name == f"{half}, {half}":
                name = half
            return name, ", ".join(parts[i:])
    return applicant.strip(), ""


def title(text: str) -> str:
    """Registry class names are shouted; the application form would not be."""
    small = {"of", "or", "and", "the"}
    words = []
    for w in text.lower().split():
        words.append(w if w in small and words else w.capitalize())
    return " ".join(words).replace("/ ", "/").replace("( ", "(")


def panel_names(ttbid: str) -> list[str]:
    """The stored panels of a COLA in display order, front first."""
    return [
        f"{ttbid}_{stem}.jpg"
        for stem in PANEL_STEMS
        if (OUT / "images" / f"{ttbid}_{stem}.jpg").exists()
    ]


def registry_row(rec: dict) -> dict:
    producer, address = split_applicant(rec.get("applicant", ""))
    is_import = rec.get("source", "").lower() != "domestic"
    origin = rec.get("origin", "").strip()
    return {
        "image": ";".join(panel_names(rec["ttbid"])),
        "beverage_type": rec["beverage_type"],
        "brand_name": rec["brand_name"],
        "class_type": title(rec["class_type"]),
        "alcohol_content": "",
        "net_contents": "",
        "producer_name": producer,
        "producer_address": address,
        "is_import": "true" if is_import else "false",
        "country_of_origin": title(origin) if is_import and origin else "",
        "scenario": "registry-as-filed",
        "expected": "corrections needed on a confident read: the class row (the registry's code, not "
        "the label's wording) and often the producer row (the permit holder's legal name); the blank "
        "alcohol content and net contents are review items",
        "ttbid": rec["ttbid"],
        "registry_class": rec["class_type"],
        "note": REGISTRY_NOTE,
    }


def apply_scenarios(rows: list[dict], scenarios: dict) -> list[dict]:
    """Overlay the hand-written scenario values on the registry rows. A scenario may only
    change the application fields; the image, type and registry columns stay."""
    allowed = {
        "brand_name",
        "class_type",
        "alcohol_content",
        "net_contents",
        "producer_name",
        "producer_address",
        "is_import",
        "country_of_origin",
    }
    unknown = set(scenarios) - {r["ttbid"] for r in rows} - {"_about"}
    if unknown:
        sys.exit(f"scenarios.json names COLAs that are not in the set: {sorted(unknown)}")
    for row in rows:
        spec = scenarios.get(row["ttbid"])
        if not spec:
            continue
        bad = set(spec["fields"]) - allowed
        if bad:
            sys.exit(f"scenario {row['ttbid']} sets columns it may not: {sorted(bad)}")
        row.update(spec["fields"])
        row["scenario"] = spec["scenario"]
        row["expected"] = spec["expected"]
        row["note"] = SCENARIO_NOTES.get(
            spec["scenario"],
            "application values transcribed by hand from the label image, then altered for "
            "the scenario",
        )
    return rows


def write_csv(records: list[dict]) -> list[dict]:
    rows = [registry_row(rec) for rec in records if panel_names(rec["ttbid"])]
    scenarios = json.loads(SCENARIOS.read_text()) if SCENARIOS.exists() else {}
    rows = apply_scenarios(rows, scenarios)
    with (OUT / "applications.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def import_downloads(inputs: list[str]) -> tuple[list[dict], int]:
    """Store every COLA's panels and metadata; returns the records kept and the bytes written."""
    scenarios = SCENARIOS.read_text() if SCENARIOS.exists() else None
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "images").mkdir(parents=True)
    if scenarios is not None:
        SCENARIOS.write_text(scenarios)
    total_bytes = 0
    kept: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        files = collect(inputs, Path(tmp))
        by_name = {p.name: p for p in files}
        records = sorted(
            (json.loads(p.read_text()) for p in files if p.suffix == ".json"),
            key=lambda r: r["ttbid"],
        )
        seen: set[str] = set()
        for rec in records:
            ttbid = rec["ttbid"]
            if ttbid in seen:
                continue
            seen.add(ttbid)
            panels = []
            for img in rec.get("images", []):
                src = by_name.get(img["file"])
                if src is None:
                    continue
                stem = Path(img["file"]).stem.split("_", 1)[1]  # front | back | other1
                panels.append((PANEL_ORDER.get(stem, 2 + len(panels)), stem, src))
            panels.sort(key=lambda t: t[0])
            if not panels or panels[0][1] != "front":
                continue
            for _, stem, src in panels[:4]:
                total_bytes += store_image(src, f"{ttbid}_{stem}.jpg")
            kept.append(rec)
    (OUT / "records.json").write_text(json.dumps(kept, indent=2) + "\n")
    return kept, total_bytes


def main(argv: list[str]) -> None:
    if argv == ["--csv-only"]:
        records = json.loads((OUT / "records.json").read_text())
        rows = write_csv(records)
        by_scenario: dict[str, int] = {}
        for row in rows:
            by_scenario[row["scenario"]] = by_scenario.get(row["scenario"], 0) + 1
        print(f"wrote {len(rows)} rows: {by_scenario}")
        return
    if not argv or argv[0].startswith("-"):
        sys.exit(__doc__)
    records, total_bytes = import_downloads(argv)
    rows = write_csv(records)
    by_type = {
        t: sum(1 for r in rows if r["beverage_type"] == t)
        for t in ("distilled_spirits", "wine", "malt_beverage")
    }
    print(
        f"kept {len(rows)} COLAs ({by_type}), {len(list((OUT / 'images').iterdir()))} images, {total_bytes / 1e6:.1f} MB"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
