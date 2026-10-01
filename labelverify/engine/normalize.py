"""Text normalizers and parsers for label fields.

Everything here is deterministic and side-effect free so the comparison rules
built on top of it can be unit tested exhaustively.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def collapse_whitespace(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def normalize_text(text: str | None) -> str:
    """Case-fold, fold unicode, drop punctuation, collapse whitespace.

    Used for fuzzy comparisons where "STONE'S THROW" and "Stone's Throw" must be equal.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("&", " and ")
    text = _PUNCT_RE.sub(" ", text)
    return collapse_whitespace(text).casefold()


def normalize_words(text: str | None) -> list[str]:
    """Word tokens after normalization, for word-level diffs."""
    return normalize_text(text).split()


# --------------------------------------------------------------------------- alcohol content

_PERCENT_RE = re.compile(
    r"(?<![\d.])(?P<num>\d{1,2}(?:[.,]\d{1,2})?)\s*%\s*"
    r"(?:alc(?:ohol)?\.?(?:\s*/\s*|\s+by\s+|\s*)vol(?:ume)?\.?|abv)?",
    re.IGNORECASE,
)
_ALC_PREFIX_RE = re.compile(
    r"alc(?:ohol)?\.?\s*(?<![\d.])(?P<num>\d{1,2}(?:[.,]\d{1,2})?)\s*%", re.IGNORECASE
)
_PROOF_RE = re.compile(
    r"(?<![\d.])(?P<num>\d{1,3}(?:[.,]\d{1,2})?)\s*(?:°\s*)?proof", re.IGNORECASE
)
_BARE_NUMBER_RE = re.compile(r"^\s*(?P<num>\d{1,2}(?:[.,]\d{1,2})?)\s*%?\s*$")


@dataclass(frozen=True)
class AlcoholContent:
    abv: float
    proof: float | None = None
    source: str = "percent"


def parse_alcohol_content(text: str | None) -> AlcoholContent | None:
    """Parse "45% Alc./Vol. (90 Proof)", "ALC. 13.5% BY VOL.", "90 proof", or a bare "45".

    Percent-by-volume wins when both appear; proof is converted (proof / 2) otherwise.
    """
    if not text:
        return None
    cleaned = unicodedata.normalize("NFKC", text)
    proof_match = _PROOF_RE.search(cleaned)
    proof = _to_float(proof_match.group("num")) if proof_match else None

    percent_match = _PERCENT_RE.search(cleaned) or _ALC_PREFIX_RE.search(cleaned)
    if percent_match:
        return AlcoholContent(abv=_to_float(percent_match.group("num")), proof=proof)
    if proof is not None:
        return AlcoholContent(abv=round(proof / 2, 2), proof=proof, source="proof")
    bare = _BARE_NUMBER_RE.match(cleaned)
    if bare:
        return AlcoholContent(abv=_to_float(bare.group("num")), source="bare")
    return None


def _to_float(raw: str) -> float:
    """ "12,5" is a European decimal; "1,000" is a thousands separator."""
    raw = re.sub(r",(?=\d{3}\b)", "", raw)
    return float(raw.replace(",", "."))


# --------------------------------------------------------------------------- net contents

METRIC_UNITS = {"ml", "cl", "l"}

_UNIT_TO_ML: dict[str, float] = {
    "ml": 1.0,
    "cl": 10.0,
    "l": 1000.0,
    "floz": 29.5735,
    "oz": 29.5735,
    "pt": 473.176,
    "qt": 946.353,
    "gal": 3785.41,
}

_UNIT_ALIASES: dict[str, str] = {
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "centiliter": "cl",
    "centiliters": "cl",
    "centilitre": "cl",
    "centilitres": "cl",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "pint": "pt",
    "pints": "pt",
    "quart": "qt",
    "quarts": "qt",
    "gallon": "gal",
    "gallons": "gal",
}

VOLUME_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>fl\.?\s*oz\.?|milliliters?|millilitres?|centiliters?|centilitres?|liters?|litres?"
    r"|ml|cl|l|oz\.?|pints?|pt\.?|quarts?|qt\.?|gallons?|gal\.?)(?![a-z])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NetContents:
    milliliters: float
    original_unit: str


def parse_net_contents(text: str | None) -> NetContents | None:
    """Parse "750 mL", "750ml", "0.75 L", "75 cL", "25.4 FL. OZ.", "1 Liter", "12 fl oz (355 mL)".

    When several volumes appear, a metric one is preferred because TTB requires metric
    for spirits and wine and it avoids fluid-ounce rounding noise.
    """
    if not text:
        return None
    cleaned = unicodedata.normalize("NFKC", text)
    candidates: list[NetContents] = []
    for match in VOLUME_RE.finditer(cleaned):
        unit_key = re.sub(r"[\s.]", "", match.group("unit")).lower()
        unit_key = _UNIT_ALIASES.get(unit_key, unit_key)
        factor = _UNIT_TO_ML.get(unit_key)
        if factor is None:
            continue
        candidates.append(
            NetContents(
                milliliters=round(_to_float(match.group("num")) * factor, 2),
                original_unit=unit_key,
            )
        )
    if not candidates:
        return None
    metric = [c for c in candidates if c.original_unit in METRIC_UNITS]
    return (metric or candidates)[0]


# --------------------------------------------------------------------------- addresses

_ADDRESS_ABBREVIATIONS: dict[str, str] = {
    "street": "st",
    "avenue": "ave",
    "road": "rd",
    "boulevard": "blvd",
    "drive": "dr",
    "lane": "ln",
    "highway": "hwy",
    "suite": "ste",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
}

_STATE_NAMES: dict[str, str] = {
    "alabama": "al",
    "alaska": "ak",
    "arizona": "az",
    "arkansas": "ar",
    "california": "ca",
    "colorado": "co",
    "connecticut": "ct",
    "delaware": "de",
    "florida": "fl",
    "georgia": "ga",
    "hawaii": "hi",
    "idaho": "id",
    "illinois": "il",
    "indiana": "in",
    "iowa": "ia",
    "kansas": "ks",
    "kentucky": "ky",
    "louisiana": "la",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "michigan": "mi",
    "minnesota": "mn",
    "mississippi": "ms",
    "missouri": "mo",
    "montana": "mt",
    "nebraska": "ne",
    "nevada": "nv",
    "new hampshire": "nh",
    "new jersey": "nj",
    "new mexico": "nm",
    "new york": "ny",
    "north carolina": "nc",
    "north dakota": "nd",
    "ohio": "oh",
    "oklahoma": "ok",
    "oregon": "or",
    "pennsylvania": "pa",
    "rhode island": "ri",
    "south carolina": "sc",
    "south dakota": "sd",
    "tennessee": "tn",
    "texas": "tx",
    "utah": "ut",
    "vermont": "vt",
    "virginia": "va",
    "washington": "wa",
    "west virginia": "wv",
    "wisconsin": "wi",
    "wyoming": "wy",
    "district of columbia": "dc",
}


STATE_NAMES = _STATE_NAMES  # name -> abbreviation, for readers that need the list
_STATE_NAME_RE = re.compile(r"\b(" + "|".join(sorted(_STATE_NAMES, key=len, reverse=True)) + r")\b")


def normalize_address(text: str | None) -> str:
    """Normalize so "123 Main Street, Louisville, Kentucky" equals "123 MAIN ST LOUISVILLE KY"."""
    normalized = _STATE_NAME_RE.sub(lambda m: _STATE_NAMES[m.group(1)], normalize_text(text))
    words = [_ADDRESS_ABBREVIATIONS.get(w, w) for w in normalized.split()]
    return " ".join(words)


# --------------------------------------------------------------------------- countries

_COUNTRY_ALIASES: dict[str, str] = {
    "usa": "united states",
    "us": "united states",
    "u s a": "united states",
    "united states of america": "united states",
    "america": "united states",
    "uk": "united kingdom",
    "great britain": "united kingdom",
    "england": "united kingdom",
    "scotland": "united kingdom",
}

_ORIGIN_PREFIX_RE = re.compile(
    r"^(?:product of|produce of|made in|imported from|bottled in|distilled in|produced in|"
    r"brewed in|distilled and bottled in|bottled and distilled in)\s+(?:the\s+)?",
    re.IGNORECASE,
)


def normalize_country(text: str | None) -> str:
    if not text:
        return ""
    stripped = _ORIGIN_PREFIX_RE.sub("", text.strip())
    normalized = normalize_text(stripped)
    return _COUNTRY_ALIASES.get(normalized, normalized)
