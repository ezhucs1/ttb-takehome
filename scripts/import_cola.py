"""Turn a folder of TTB Public COLA Registry downloads into the bundled test set.

Input: one or more folders (or zips) as produced by a registry fetch: ``<ttbid>.json``
with the registry metadata, ``<ttbid>_front.*``, ``<ttbid>_back.*``, ``<ttbid>_other<n>.*``
images. Output: ``labelverify/testdata/cola/`` with every image resized for the reader
and re-encoded as JPEG (the registry's PNGs are large), one ``records.json`` with the
registry metadata, ``applications.csv`` in the batch upload's column format, and a
README that states the provenance.

    .venv/bin/python scripts/import_cola.py cola_part1.zip cola_part2.zip
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
    "ttbid",
    "registry_class",
    "note",
]
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
            return ", ".join(parts[:i]), ", ".join(parts[i:])
    return applicant.strip(), ""


def title(text: str) -> str:
    """Registry class names are shouted; the application form would not be."""
    small = {"of", "or", "and", "the"}
    words = []
    for w in text.lower().split():
        words.append(w if w in small and words else w.capitalize())
    return " ".join(words).replace("/ ", "/").replace("( ", "(")


def main(inputs: list[str]) -> None:
    if not inputs:
        sys.exit(__doc__)
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "images").mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        files = collect(inputs, Path(tmp))
        by_name = {p.name: p for p in files}
        records = sorted(
            (json.loads(p.read_text()) for p in files if p.suffix == ".json"),
            key=lambda r: r["ttbid"],
        )
        seen: set[str] = set()
        rows = []
        total_bytes = 0
        kept = 0
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
            names = []
            for _, stem, src in panels[:4]:
                name = f"{ttbid}_{stem}.jpg"
                total_bytes += store_image(src, name)
                names.append(name)
            kept += 1
            producer, address = split_applicant(rec.get("applicant", ""))
            is_import = rec.get("source", "").lower() != "domestic"
            origin = rec.get("origin", "").strip()
            rows.append(
                {
                    "image": ";".join(names),
                    "beverage_type": rec["beverage_type"],
                    "brand_name": rec["brand_name"],
                    "class_type": title(rec["class_type"]),
                    "alcohol_content": "",
                    "net_contents": "",
                    "producer_name": producer,
                    "producer_address": address,
                    "is_import": "true" if is_import else "false",
                    "country_of_origin": title(origin) if is_import and origin else "",
                    "ttbid": ttbid,
                    "registry_class": rec["class_type"],
                    "note": "registry metadata; alcohol content and net contents are not on the "
                    "public record and are left blank",
                }
            )
        (OUT / "records.json").write_text(json.dumps(records, indent=2) + "\n")
        with (OUT / "applications.csv").open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    by_type = {
        t: sum(1 for r in rows if r["beverage_type"] == t)
        for t in ("distilled_spirits", "wine", "malt_beverage")
    }
    print(
        f"kept {kept} COLAs ({by_type}), {len(list((OUT / 'images').iterdir()))} images, {total_bytes / 1e6:.1f} MB"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
