"""Local OCR extractor: Tesseract plus rule-based field classification.

This is the fallback for environments that block outbound traffic to model APIs.
It is noticeably less accurate than the vision model on real photos, so every field
it produces carries a low confidence and lands in the "needs review" band.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from io import BytesIO

from ..models import ExtractedField, HealthWarningExtraction, ImageQuality, LabelExtraction
from ..normalize import _VOLUME_RE
from .base import ExtractionError, Panel

_WARNING_RE = re.compile(
    r"government\s+warning\s*:?.*?(?:health\s+problems\.?|machinery[^\n]*)",
    re.IGNORECASE | re.DOTALL,
)
_ALCOHOL_RE = re.compile(
    r"(?:alc(?:ohol)?\.?\s*)?\d{1,2}(?:[.,]\d{1,2})?\s*%\s*(?:alc(?:ohol)?\.?(?:\s*/\s*|\s+by\s+|\s*)vol(?:ume)?\.?|abv)?"
    r"(?:\s*\(?\s*\d{1,3}\s*proof\s*\)?)?|\d{1,3}\s*proof",
    re.IGNORECASE,
)
_COUNTRY_RE = re.compile(
    r"(?:product|produce)\s+of\s+([A-Za-z .]+?)(?:[\n,.]|$)|imported\s+from\s+([A-Za-z .]+?)(?:[\n,.]|$)",
    re.IGNORECASE,
)
_PRODUCER_RE = re.compile(
    r"(?:distilled|bottled|produced|brewed|vinted|imported|made|blended)"
    r"(?:\s*(?:,|and|&)\s*(?:distilled|bottled|produced|brewed|vinted|imported|made|blended))*"
    r"\s+by\s+(?P<name>[^\n]+)",
    re.IGNORECASE,
)
_ADDRESS_HINT_RE = re.compile(r"\b[A-Z]{2}\b\s*\d{5}|\b\d{5}\b|,\s*[A-Z]{2}\b")

_CLASS_KEYWORDS = (
    "bourbon",
    "whiskey",
    "whisky",
    "vodka",
    "gin",
    "rum",
    "tequila",
    "mezcal",
    "brandy",
    "cognac",
    "liqueur",
    "scotch",
    "rye",
    "wine",
    "cabernet",
    "merlot",
    "chardonnay",
    "pinot",
    "sauvignon",
    "riesling",
    "zinfandel",
    "rosé",
    "rose",
    "sparkling",
    "champagne",
    "ale",
    "lager",
    "ipa",
    "stout",
    "porter",
    "pilsner",
    "beer",
    "cider",
    "malt beverage",
    "hard seltzer",
)

_LOW = 0.45


class TesseractExtractor:
    name = "tesseract"

    def extract(self, image: bytes, media_type: str) -> LabelExtraction:
        return self.extract_panels([(image, media_type)])

    def extract_panels(self, panels: Sequence[Panel]) -> LabelExtraction:
        """OCR every panel, concatenate the text, then classify it once."""
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ExtractionError("pytesseract is not installed.") from exc
        texts: list[str] = []
        for image, _ in panels:
            try:
                texts.append(pytesseract.image_to_string(Image.open(BytesIO(image))))
            except pytesseract.TesseractNotFoundError as exc:
                raise ExtractionError(
                    "The tesseract binary is not installed on this machine."
                ) from exc
        return classify_text("\n\n".join(texts))


def classify_text(text: str) -> LabelExtraction:
    """Assign raw OCR text to TTB fields using regexes and keyword heuristics."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    consumed: set[str] = set()

    warning = _extract_warning(text)
    if warning.text:
        for ln in lines:
            if ln in warning.text:
                consumed.add(ln)

    alcohol = _first_match(_ALCOHOL_RE, text)
    net = None
    for m in _VOLUME_RE.finditer(text):
        net = m.group(0)
        break

    country = None
    if (cm := _COUNTRY_RE.search(text)) is not None:
        country = (cm.group(1) or cm.group(2) or "").strip() or None

    producer_name = None
    producer_address = None
    if (pm := _PRODUCER_RE.search(text)) is not None:
        producer_name = pm.group("name").strip().rstrip(",")
        consumed.add(producer_name)
        producer_line_idx = next(
            (i for i, ln in enumerate(lines) if pm.group("name").strip() in ln), None
        )
        if producer_line_idx is not None:
            for candidate in lines[producer_line_idx + 1 : producer_line_idx + 3]:
                if _ADDRESS_HINT_RE.search(candidate) and candidate not in consumed:
                    producer_address = candidate
                    consumed.add(candidate)
                    break

    class_line = _best_class_line(lines, consumed)
    if class_line:
        consumed.add(class_line)

    brand_line = next(
        (
            ln
            for ln in lines
            if ln not in consumed
            and re.search(r"[A-Za-z]{2,}", ln)
            and not _ALCOHOL_RE.search(ln)
            and not _VOLUME_RE.search(ln)
            and "warning" not in ln.lower()
        ),
        None,
    )

    def field(value: str | None, confidence: float = _LOW) -> ExtractedField:
        return ExtractedField(value=value, confidence=confidence if value else 0.0)

    return LabelExtraction(
        brand_name=field(brand_line, 0.35),
        class_type=field(class_line),
        alcohol_content=field(alcohol, 0.55),
        net_contents=field(net, 0.55),
        producer_name=field(producer_name),
        producer_address=field(producer_address, 0.4),
        country_of_origin=field(country),
        health_warning=warning,
        image_quality=ImageQuality(
            readable=bool(lines),
            issues=[] if lines else ["No text could be read from the image."],
        ),
    )


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    return m.group(0).strip() if m else None


def _extract_warning(text: str) -> HealthWarningExtraction:
    m = _WARNING_RE.search(text)
    if not m:
        return HealthWarningExtraction(present=False, confidence=0.0)
    statement = re.sub(r"\s+", " ", m.group(0)).strip()
    heading = statement[: len("GOVERNMENT WARNING")]
    return HealthWarningExtraction(
        present=True,
        text=statement,
        heading_all_caps=heading == heading.upper(),
        heading_bold=None,
        confidence=_LOW,
    )


def _best_class_line(lines: list[str], consumed: set[str]) -> str | None:
    best, best_hits = None, 0
    for ln in lines:
        if ln in consumed or "warning" in ln.lower():
            continue
        lowered = ln.lower()
        hits = sum(1 for kw in _CLASS_KEYWORDS if kw in lowered)
        if hits > best_hits:
            best, best_hits = ln, hits
    return best
