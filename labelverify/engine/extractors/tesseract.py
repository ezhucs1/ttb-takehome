"""Local OCR extractor: Tesseract plus rule-based field classification.

This is the fallback for environments that block outbound traffic to model APIs.
It is noticeably less accurate than the vision model on real photos, so every field
it produces carries a low confidence and lands in the "needs review" band.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Sequence
from io import BytesIO

from ..models import ExtractedField, HealthWarningExtraction, ImageQuality, LabelExtraction
from ..normalize import STATE_NAMES, VOLUME_RE
from .base import ExtractionError, Panel

# The full statement first; the shorter ending only when OCR lost the last words. One
# pattern with both endings stops at "machinery" whenever "health problems." sits on
# the next line, which is how most labels wrap it.
_WARNING_RE = re.compile(
    r"government\s+warning\s*:?.*?health\s+problems\.?", re.IGNORECASE | re.DOTALL
)
_WARNING_CUT_RE = re.compile(
    r"government\s+warning\s*:?.*?machinery[^\n]*", re.IGNORECASE | re.DOTALL
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
_ADDRESS_HINT_RE = re.compile(
    r"\b[A-Z]{2}\b\s*\d{5}|\b\d{5}\b|,\s*[A-Z]{2}\b|,\s*(?:"
    + "|".join(sorted((n for n in STATE_NAMES), key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)

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


# Where the binary usually lands when it is not on the service's PATH: Homebrew on
# Apple silicon and Intel Macs, snap, and the apt and Windows installers.
_USUAL_PLACES = (
    "/opt/homebrew/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/usr/bin/tesseract",
    "/snap/bin/tesseract",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)


def tesseract_command() -> str | None:
    """The tesseract executable: LABELVERIFY_TESSERACT_CMD, else PATH, else the usual
    install locations. None when nothing is found."""
    configured = os.environ.get("LABELVERIFY_TESSERACT_CMD", "").strip()
    if configured:
        return configured if os.path.exists(configured) else shutil.which(configured)
    found = shutil.which("tesseract")
    if found:
        return found
    return next((place for place in _USUAL_PLACES if os.path.exists(place)), None)


class TesseractExtractor:
    name = "tesseract"

    @staticmethod
    def available() -> bool:
        """True when the tesseract binary can be found."""
        return tesseract_command() is not None

    def extract(self, image: bytes, media_type: str) -> LabelExtraction:
        return self.extract_panels([(image, media_type)])

    def extract_panels(self, panels: Sequence[Panel]) -> LabelExtraction:
        """OCR every panel, concatenate the text, then classify it once."""
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ExtractionError("pytesseract is not installed.") from exc
        command = tesseract_command()
        if command is None:
            raise ExtractionError(
                "The tesseract binary was not found on this machine's PATH "
                f"({os.environ.get('PATH', '')}). Install tesseract-ocr, or set "
                "LABELVERIFY_TESSERACT_CMD to the full path of the executable."
            )
        pytesseract.pytesseract.tesseract_cmd = command
        # Tesseract spawns a thread per core for every call. The batch worker runs several
        # reads at once, and five four-thread processes on four cores thrash: a load of 20
        # and reads that take minutes. One thread per call keeps a read at about a second
        # and lets the worker's own concurrency do the parallel work.
        os.environ.setdefault("OMP_THREAD_LIMIT", "1")
        texts: list[str] = []
        prominent: list[str] = []
        for n, (image, _) in enumerate(panels):
            try:
                text, data = _best_ocr(Image.open(BytesIO(image)))
                texts.append(text)
                if n == 0:  # the brand is on the front panel
                    prominent = prominent_lines(data)
            except pytesseract.TesseractNotFoundError as exc:
                raise ExtractionError(
                    f"The tesseract binary at {command} could not be run ({exc})."
                ) from exc
        return classify_text("\n\n".join(texts), prominent_lines=prominent)


def _best_ocr(image) -> tuple[str, dict]:
    """OCR the panel as is and inverted: Tesseract wants dark text on a light ground, and
    cans and dark labels print the other way round, often only for the brand. The text
    comes from the pass that read more confident words; the word boxes for the brand
    come from the pass whose largest type is larger, since that is where the brand is."""
    import pytesseract
    from PIL import ImageOps

    def run(img) -> tuple[str, dict, int, float]:
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        good = 0
        tallest = 0.0
        for word, conf, height in zip(data["text"], data["conf"], data["height"], strict=True):
            if str(word).strip() and float(conf) >= 60:
                good += 1
                tallest = max(tallest, float(height))
        return pytesseract.image_to_string(img), data, good, tallest

    plain = run(image)
    if plain[2] >= 40:  # a full read of a light label; the inverse pass would only cost time
        return plain[0], plain[1]
    inverted = run(ImageOps.invert(image.convert("RGB")))
    text = (inverted if inverted[2] > plain[2] else plain)[0]
    data = (inverted if inverted[3] > plain[3] else plain)[1]
    return text, data


def prominent_lines(data: dict, *, ratio: float = 0.72) -> list[str]:
    """The text set in the largest type, in reading order, from Tesseract's word boxes.

    Words are grouped into the lines Tesseract found; a line's size is the mean height of
    its words. The tallest line seeds the brand; lines directly above or below it that are
    nearly as tall join it, so a two-line brand ("OLD TOM" / "DISTILLERY") comes back as
    one candidate while the kicker above it, a medal, or a footer do not. The remaining
    lines follow, tallest first, as further candidates.
    """
    lines: dict[tuple[int, int, int], list[tuple[str, int, int]]] = {}
    for i, word in enumerate(data.get("text", [])):
        if not str(word).strip() or int(float(data["conf"][i])) < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append((str(word), int(data["height"][i]), int(data["top"][i])))
    sized = []
    for words in lines.values():
        tokens = [w for w, _, _ in words if re.search(r"[A-Za-z]{2,}", w)]  # OCR noise out
        if not tokens:
            continue
        sized.append(
            (
                " ".join(tokens),
                sum(h for _, h, _ in words) / len(words),
                min(t for _, _, t in words),
            )
        )
    if not sized:
        return []
    sized.sort(key=lambda item: item[2])  # top to bottom
    seed = max(range(len(sized)), key=lambda i: sized[i][1])
    tallest = sized[seed][1]
    lo = hi = seed
    while (
        lo > 0
        and sized[lo - 1][1] >= ratio * tallest
        and sized[lo][2] - sized[lo - 1][2] < 2.2 * tallest
    ):
        lo -= 1
    while (
        hi + 1 < len(sized)
        and sized[hi + 1][1] >= ratio * tallest
        and sized[hi + 1][2] - sized[hi][2] < 2.2 * tallest
    ):
        hi += 1
    brand = " ".join(text for text, _, _ in sized[lo : hi + 1])
    rest = sorted(
        (item for i, item in enumerate(sized) if not lo <= i <= hi), key=lambda item: -item[1]
    )
    return [brand] + [text for text, _, _ in rest]


_SULFITE_RE = re.compile(r"contains\s+sulf(?:ph)?ites", re.IGNORECASE)
_QUALIFYING_RE = re.compile(
    r"\b((?:distilled|bottled|produced|brewed|vinted|cellared|imported|made|canned|blended)"
    r"(?:,?\s*(?:and|&)\s*\w+)?\s+by)\b",
    re.IGNORECASE,
)
_IMPORTER_RE = re.compile(r"\b(imported\s+by\s+[^\n]{3,80})", re.IGNORECASE)
_AGE_RE = re.compile(r"\b(aged\s+\w+\s+(?:years?|months?)|\d+\s+years?\s+old)\b", re.IGNORECASE)
_BOND_RE = re.compile(r"\b(bottled[\s-]+in[\s-]+bond|bonded)\b", re.IGNORECASE)
_BLEND_PCT_RE = re.compile(
    r"\b(\d{1,3}\s*%\s+(?:straight\s+)?\w+(?:\s+\w+){0,3}\s+whisk(?:e)?y)\b", re.IGNORECASE
)
_VINTAGE_RE = re.compile(r"(?im)^\s*(?:vintage\s+)?((?:19|20)\d{2})\s*$")  # a year on its own line
_ESTATE_RE = re.compile(r"\b(estate\s+bottled)\b", re.IGNORECASE)
# A short list of well-known appellations; OCR cannot tell a place name from any other
# capitalized words, so this is a best-effort fallback the model reader does properly.
_APPELLATION_RE = re.compile(
    r"\b(napa valley|sonoma (?:coast|county|valley)|russian river valley|paso robles|"
    r"santa barbara county|central coast|willamette valley|columbia valley|finger lakes|"
    r"lodi|california|oregon|washington|new york|texas|virginia)\b",
    re.IGNORECASE,
)
_STRENGTH_RE = re.compile(
    r"\b(extra[\s-]+strength|full[\s-]+strength|high[\s-]+test|high[\s-]+proof|strong)\b",
    re.IGNORECASE,
)
_CATEGORY_CUES = (
    (
        "distilled_spirits",
        re.compile(r"\b(distilled|proof|whisk(e)?y|bourbon|vodka|gin|rum|tequila)\b", re.I),
    ),
    (
        "wine",
        re.compile(r"\b(wine|vint(ed|age)|sulfites|cabernet|chardonnay|merlot|ros[eé])\b", re.I),
    ),
    ("malt_beverage", re.compile(r"\b(brewed|beer|ale|lager|ipa|stout|porter|malt)\b", re.I)),
)


def _category_from_text(text: str) -> str | None:
    """The class with the most cue hits in the OCR text, or None on a tie at zero."""
    scores = {name: len(pattern.findall(text)) for name, pattern in _CATEGORY_CUES}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def classify_text(text: str, *, prominent_lines: list[str] | None = None) -> LabelExtraction:
    """Assign raw OCR text to TTB fields using regexes and keyword heuristics.

    ``prominent_lines`` (largest type first, from the word boxes) names the brand when
    it is given; without it the first unclaimed line is taken, which on many labels is
    the kicker above the brand rather than the brand itself.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    consumed: set[str] = set()

    warning = _extract_warning(text)
    if warning.text:
        for ln in lines:
            if ln in warning.text:
                consumed.add(ln)

    alcohol = _first_match(_ALCOHOL_RE, text)
    net = None
    for m in VOLUME_RE.finditer(text):
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

    def could_be_brand(ln: str) -> bool:
        lowered = ln.lower()
        return bool(
            re.search(r"[A-Za-z]{2,}", ln)
            and not _ALCOHOL_RE.search(ln)
            and not VOLUME_RE.search(ln)
            and "warning" not in lowered
            and not _BOND_RE.search(ln)
            and not _APPELLATION_RE.fullmatch(ln.strip(" ·.-"))
            and (class_line is None or ln.lower() != class_line.lower())
            and (producer_address is None or ln.lower() != producer_address.lower())
        )  # the brand may equal the producer's name ("Old Tom Distillery" is both)

    brand_line = next((ln for ln in (prominent_lines or []) if could_be_brand(ln)), None)
    if brand_line is None:
        brand_line = next((ln for ln in lines if ln not in consumed and could_be_brand(ln)), None)

    def field(value: str | None, confidence: float = _LOW) -> ExtractedField:
        return ExtractedField(value=value, confidence=confidence if value else 0.0)

    sulfites = _first_match(_SULFITE_RE, text)
    category = _category_from_text(text)
    return LabelExtraction(
        brand_name=field(brand_line, 0.35),
        class_type=field(class_line),
        alcohol_content=field(alcohol, 0.55),
        net_contents=field(net, 0.55),
        producer_name=field(producer_name),
        producer_address=field(producer_address, 0.4),
        country_of_origin=field(country),
        sulfite_declaration=field(sulfites, 0.6),
        qualifying_phrase=field(_first_match(_QUALIFYING_RE, text), 0.5),
        importer_statement=field(_first_match(_IMPORTER_RE, text), 0.5),
        age_statement=field(_first_match(_AGE_RE, text), 0.5),
        bottled_in_bond_claim=field(_first_match(_BOND_RE, text), 0.5),
        blend_percentage=field(_first_match(_BLEND_PCT_RE, text), 0.5),
        appellation=field(_first_match(_APPELLATION_RE, text), 0.4),
        vintage_year=field(_first_match(_VINTAGE_RE, text), 0.5),
        estate_bottled_claim=field(_first_match(_ESTATE_RE, text), 0.5),
        strength_claim=field(_first_match(_STRENGTH_RE, text), 0.5),
        product_category=field(category, 0.6 if category else 0.0),
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
    m = _WARNING_RE.search(text) or _WARNING_CUT_RE.search(text)
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
