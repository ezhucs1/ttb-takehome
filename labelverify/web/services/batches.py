"""Batch upload: CSV template and parsing, background processing, results export."""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from ...engine.extractors import Extractor
from ...engine.models import (
    ApplicationData,
)
from ...engine.rules import STATEMENT_SOURCES
from ..models import (
    Application,
    Batch,
    BatchItem,
    User,
    utcnow,
)
from .common import BATCH_CONCURRENCY, BATCH_MAX_ROWS, MAX_UPLOAD_BYTES, WorkflowError, result_of
from .runs import Upload, label_set_of, prepare_uploads, store_run, verify_label_set
from .workflow import create_draft, submit

log = logging.getLogger(__name__)

BATCH_COLUMNS = [
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
]


def split_image_names(cell: str) -> list[str]:
    """The image column holds one file name, or several separated by ';' or '|'."""
    return [n.strip() for n in re.split(r"[;|]", cell or "") if n.strip()]


def batch_template_csv() -> str:
    """The header plus one example row per class, and one that leaves the type blank so the
    label decides. The columns are the same for every class; what differs is which ones
    the class requires, and that is explained next to the download."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(BATCH_COLUMNS)
    writer.writerows(
        [
            [
                "old-tom-bourbon.jpg",
                "distilled_spirits",
                "OLD TOM DISTILLERY",
                "Kentucky Straight Bourbon Whiskey",
                "45% Alc./Vol. (90 Proof)",
                "750 mL",
                "Old Tom Distillery",
                "Bardstown, KY 40004",
                "false",
                "",
            ],
            [
                "stones-throw-wine.jpg",
                "wine",
                "Stone's Throw",
                "Cabernet Sauvignon",
                "14.5",
                "750 mL",
                "Stone's Throw Vineyards",
                "St. Helena, CA",
                "false",
                "",
            ],
            [
                "harbor-light-ipa.jpg",
                "malt_beverage",
                "Harbor Light",
                "India Pale Ale",
                "",
                "12 fl oz",
                "Harbor Light Brewing Co.",
                "Portland, ME 04101",
                "false",
                "",
            ],
            [
                "glen-aldie-front.jpg;glen-aldie-back.jpg",
                "",
                "Glen Aldie",
                "Single Malt Scotch Whisky",
                "43%",
                "700 mL",
                "Glen Aldie Distillers",
                "Speyside, Scotland",
                "true",
                "United Kingdom",
            ],
        ]
    )
    return buffer.getvalue()


# Two deliberately broken rows in the sample batch, so the demo shows what the pipeline
# does with bad input: a photo that is not a label, and a row whose image is not in the zip.
NOT_A_LABEL = "not-a-label.jpg"
MISSING_IMAGE = "missing-photo.jpg"
# Rows of the sample batch that leave the type blank, so the demo shows it being read off
# the label and the matching rules applied.
SAMPLE_BATCH_UNTYPED = {"stones-throw-wine", "harbor-light-ipa-net-contents"}
SAMPLE_BATCH_EXTRA_ROWS = [
    [NOT_A_LABEL, "wine", "Harbor Mist", "Red Wine", "13%", "750 mL", "", "", "false", ""],
    [MISSING_IMAGE, "malt_beverage", "Night Shift", "Lager", "5%", "12 fl oz", "", "", "false", ""],
]


def not_a_label_image() -> bytes:
    """A dark, noisy photograph of nothing: readable as an image, useless as a label."""
    from PIL import Image, ImageFilter

    noise = Image.effect_noise((900, 1200), 48).convert("RGB")
    shade = Image.linear_gradient("L").resize((900, 1200)).convert("RGB")
    image = (
        Image.blend(noise, shade, 0.6)
        .point(lambda v: int(v * 0.35))
        .filter(ImageFilter.GaussianBlur(3))
    )
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=80)
    return out.getvalue()


def sample_batch_csv(samples) -> str:
    """One row per bundled sample label plus two broken rows, in the template's columns."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(BATCH_COLUMNS)
    for row in SAMPLE_BATCH_EXTRA_ROWS:
        writer.writerow(row)
    for s in samples:
        a = s["application"]
        writer.writerow(
            [
                s["file"],
                "" if s["id"] in SAMPLE_BATCH_UNTYPED else a.get("beverage_type", ""),
                a.get("brand_name", ""),
                a.get("class_type", ""),
                a.get("alcohol_content", ""),
                a.get("net_contents", ""),
                a.get("producer_name", ""),
                a.get("producer_address", ""),
                "true" if a.get("is_import") else "false",
                a.get("country_of_origin", "") or "",
            ]
        )
    return buffer.getvalue()


def sample_batch_zip(samples, samples_dir) -> bytes:
    """The bundled sample images, zipped under the names the sample CSV refers to."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(NOT_A_LABEL, not_a_label_image())
        for s in samples:
            archive.write(samples_dir / s["file"], arcname=s["file"])
    return buffer.getvalue()


# Sixty approved labels from the TTB Public COLA Registry, with the registry's own
# application values, bundled as a second batch to try (see labelverify/testdata/cola).
COLA_DIR = Path(__file__).resolve().parents[2] / "testdata" / "cola"


def cola_batch_csv() -> str:
    return (COLA_DIR / "applications.csv").read_text()


def cola_batch_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:  # JPEGs: no gain from deflate
        for image in sorted((COLA_DIR / "images").iterdir()):
            archive.write(image, arcname=image.name)
    return buffer.getvalue()


def cola_row_count() -> int:
    return sum(1 for line in cola_batch_csv().splitlines()[1:] if line.strip())


@dataclass
class ParsedBatch:
    rows: list[dict]
    images: dict[str, bytes]
    errors: list[str]


def parse_batch(csv_bytes: bytes, zip_bytes: bytes) -> ParsedBatch:
    errors: list[str] = []
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ParsedBatch([], {}, ["The CSV must be UTF-8 encoded."])
    reader = csv.DictReader(io.StringIO(text))
    missing = [
        c
        for c in ("image", "beverage_type", "brand_name", "class_type")
        if c not in (reader.fieldnames or [])
    ]
    if missing:
        return ParsedBatch([], {}, [f"CSV is missing required column(s): {', '.join(missing)}."])
    rows = [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]
    if not rows:
        errors.append("The CSV has no data rows.")
    if len(rows) > BATCH_MAX_ROWS:
        errors.append(f"The CSV has {len(rows)} rows; the limit is {BATCH_MAX_ROWS} per batch.")

    images: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename.rsplit("/", 1)[-1]
                if name.startswith(".") or not name.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".webp")
                ):
                    continue
                if info.file_size > MAX_UPLOAD_BYTES:  # checked before anything is inflated
                    errors.append(f"Image '{name}' is larger than 10 MB.")
                    continue
                images[name] = zf.read(info)
    except zipfile.BadZipFile:
        errors.append("The images file is not a valid .zip archive.")
    return ParsedBatch(rows, images, errors)


def create_batch(db: Session, applicant: User, filename: str, parsed: ParsedBatch) -> Batch:
    batch = Batch(applicant_id=applicant.id, filename=filename, total=len(parsed.rows))
    db.add(batch)
    db.flush()
    for n, row in enumerate(parsed.rows, start=1):
        db.add(
            BatchItem(
                batch_id=batch.id,
                row_number=n,
                brand_name=row.get("brand_name", ""),
                image_name=row.get("image", ""),
            )
        )
    db.flush()
    return batch


def process_batch(
    session_factory: sessionmaker,
    batch_id: str,
    parsed: ParsedBatch,
    extractor: Extractor,
    fallback: Extractor | None = None,
) -> None:
    """Verify every row with bounded concurrency. Each row uses its own session."""
    with session_factory() as db:
        batch = db.get(Batch, batch_id)
        applicant = db.get(User, batch.applicant_id)
        items = [(item.id, item.row_number) for item in batch.items]

    def row_inputs(row: dict) -> tuple[ApplicationData, list[Upload]]:
        names = split_image_names(row.get("image", ""))
        if not names:
            raise WorkflowError("The image column is empty.")
        uploads: list[Upload] = []
        for name in names:
            image = parsed.images.get(name)
            if image is None:
                raise WorkflowError(f"Image '{name}' was not found in the zip.")
            uploads.append((image, name))
        data = ApplicationData(
            beverage_type=row.get("beverage_type", "") or None,  # blank: the label decides
            brand_name=row.get("brand_name", ""),
            class_type=row.get("class_type", ""),
            alcohol_content=row.get("alcohol_content", ""),
            net_contents=row.get("net_contents", ""),
            producer_name=row.get("producer_name", ""),
            producer_address=row.get("producer_address", ""),
            is_import=row.get("is_import", "").lower() in ("true", "yes", "1", "y"),
            country_of_origin=row.get("country_of_origin", ""),
            # Optional columns: a statement column that is absent means "not filed", and
            # the label is checked against the rules alone.
            **{field: row.get(field) for field in STATEMENT_SOURCES},
        )
        return data, uploads

    def mark(item_id: str, *, application_id: str | None, error: str | None) -> None:
        """Record a row's outcome and bump the batch counter, in one short transaction."""
        with session_factory() as db:
            item = db.get(BatchItem, item_id)
            item.status = "error" if error else "done"
            item.error = error
            if application_id:
                item.application_id = application_id
            column = Batch.failed if error else Batch.completed
            db.execute(update(Batch).where(Batch.id == batch_id).values({column.key: column + 1}))
            db.commit()

    def work(item_id: str, row_number: int) -> None:
        """Three steps, two short transactions: the model call in the middle holds no lock.
        SQLite allows one writer at a time, and a model read takes seconds."""
        app_id: str | None = None
        try:
            data, uploads = row_inputs(parsed.rows[row_number - 1])
            prepared = prepare_uploads(uploads)  # decode and resize before taking the write lock
            with session_factory() as db:  # 1. the draft and its images
                app = create_draft(db, applicant, data, uploads, prepared=prepared)
                app.batch_id = batch_id
                label_set = label_set_of(app)
                app_id = app.id
                db.commit()
            result, error = verify_label_set(label_set, extractor, fallback=fallback)  # 2.
            with session_factory() as db:  # 3. the result, then into the queue
                app = db.get(Application, app_id)
                store_run(db, app, label_set, extractor.name, "batch", result, error)
                submit(db, app, applicant)
                db.commit()
            mark(item_id, application_id=app_id, error=None)
        except Exception as exc:  # one bad row must not sink the batch
            log.exception("batch row %s failed", row_number)
            try:
                mark(item_id, application_id=app_id, error=str(exc))
            except Exception:  # the finalizer below still accounts for this row
                log.exception("batch row %s could not record its failure", row_number)

    try:
        with ThreadPoolExecutor(max_workers=BATCH_CONCURRENCY) as pool:
            futures = [pool.submit(work, item_id, row_number) for item_id, row_number in items]
            for future in futures:
                future.result()  # work() never raises; this surfaces anything unexpected
    finally:
        _finish_batch(session_factory, batch_id)


def finish_stale_batches(session_factory: sessionmaker) -> int:
    """Close every batch still marked processing: its worker died with the previous
    process, and nothing else would ever stop the page from saying 'working'."""
    with session_factory() as db:
        stale = list(db.scalars(select(Batch.id).where(Batch.status == "processing")))
    for batch_id in stale:
        _finish_batch(session_factory, batch_id)
    return len(stale)


def _finish_batch(session_factory: sessionmaker, batch_id: str) -> None:
    """Close the batch whatever happened: rows still pending are marked, counters are
    recomputed from the rows, and the page stops saying 'working'."""
    with session_factory() as db:
        batch = db.get(Batch, batch_id)
        if batch is None:
            return
        for item in batch.items:
            if item.status == "pending":
                item.status = "error"
                item.error = "This row did not finish; start the batch again for it."
        batch.completed = sum(1 for i in batch.items if i.status == "done")
        batch.failed = sum(1 for i in batch.items if i.status == "error")
        batch.status = "done"
        batch.finished_at = utcnow()
        db.commit()


RESULT_WORDS = {
    "approve": "all fields match",
    "needs_review": "needs a look",
    "request_correction": "corrections needed",
    "error": "could not be checked",
}


def batch_results_csv(batch: Batch) -> str:
    """One line per row of the batch with the check's outcome and every flagged field, so a
    batch can be reviewed, filed or shared outside the app."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
            "row",
            "image",
            "brand_as_filed",
            "application",
            "result",
            "reader",
            "flagged_fields",
            "details",
        ]
    )
    for item in batch.items:
        app = item.application
        run = app.latest_run if app else None
        result = result_of(run)
        if item.status == "error" or app is None:
            writer.writerow(
                [
                    item.row_number,
                    item.image_name,
                    item.brand_name,
                    "",
                    "could not be checked",
                    "",
                    "",
                    item.error or "",
                ]
            )
            continue
        flagged = [
            f
            for f in (result.fields if result else [])
            if f.verdict.value in ("mismatch", "needs_review")
        ]
        writer.writerow(
            [
                item.row_number,
                item.image_name,
                item.brand_name,
                app.serial,
                RESULT_WORDS.get(app.recommendation or "error", app.recommendation or ""),
                run.extractor if run else "",
                "; ".join(f"{f.field}={f.verdict.value}" for f in flagged),
                " | ".join(
                    f"{f.label}: application '{f.application_value}' vs label "
                    f"'{f.label_value or ''}'. {f.reason}"
                    for f in flagged
                )
                or (run.error if run and run.error else ""),
            ]
        )
    return out.getvalue()


def batch_summary(batch: Batch) -> dict[str, int]:
    counts = {"approve": 0, "needs_review": 0, "request_correction": 0, "error": 0}
    for item in batch.items:
        if item.status == "error" or item.application is None:
            counts["error"] += 1
        else:
            counts[item.application.recommendation or "error"] = (
                counts.get(item.application.recommendation or "error", 0) + 1
            )
    return counts
