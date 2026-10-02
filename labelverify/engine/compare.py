"""Field-by-field comparison of application data against a label extraction.

Each field has its own strategy, mirroring how a labeling specialist reads a label:

* Brand name, class/type, producer: fuzzy after normalization, so "STONE'S THROW"
  equals "Stone's Throw" but "Stone's Throw" does not equal "Stone Ridge".
* Alcohol content: numeric, with proof converted to percent alcohol by volume.
* Net contents: numeric, converted to milliliters.
* Country of origin: exact after alias normalization, required only for imports.
* Health warning: word for word, with capitalization and bold checks (see ``warning.py``).

Nothing here calls a model. All judgment calls are thresholds that can be tuned and tested.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from datetime import date

from rapidfuzz import fuzz

from .models import (
    ApplicationData,
    BeverageType,
    ExtractedField,
    FieldResult,
    LabelExtraction,
    Recommendation,
    Verdict,
    VerificationResult,
)
from .normalize import (
    METRIC_UNITS,
    normalize_address,
    normalize_country,
    normalize_producer,
    normalize_text,
    parse_alcohol_content,
    parse_net_contents,
    producer_candidates,
)
from .rules import ClassRules, Requirement, rules_for
from .warning import check_health_warning

# Text fields match only when they are identical after normalization (case, punctuation,
# spacing, known synonyms). Anything else is a real difference: above the REVIEW threshold
# (0-100 similarity) it goes to a human as "similar but not identical", below it is a mismatch.
# This keeps judgment calls such as typos and reordered words with the specialist.
BRAND_REVIEW = 75.0
CLASS_REVIEW = 70.0
PRODUCER_REVIEW = 70.0
ADDRESS_REVIEW = 65.0

# Extractions below this confidence never produce an unattended "match".
LOW_CONFIDENCE = 0.6

# Two values are "the same" within reading slack; anything beyond that is a difference,
# which the class's labeling tolerance then grades as review (within tolerance) or mismatch.
ABV_TOLERANCE = 0.05  # percentage points
VOLUME_TOLERANCE_ML = 1.0

# The model's own view of the product class is used only when the class/type words do not
# settle it, and only when the model is reasonably sure.
CATEGORY_CONFIDENCE = 0.6

_CLASS_SYNONYMS: dict[str, str] = {
    "whisky": "whiskey",
    "cabernet sauvignon": "cabernet",
    "india pale ale": "ipa",
}
_CLASS_SYNONYM_RE = re.compile(
    r"\b(" + "|".join(sorted(_CLASS_SYNONYMS, key=len, reverse=True)) + r")\b"
)


def _apply_synonyms(text: str) -> str:
    """Spellings and short forms that name the same class, folded to one form."""
    return _CLASS_SYNONYM_RE.sub(lambda m: _CLASS_SYNONYMS[m.group(1)], text)


def _normalize_class(text: str | None) -> str:
    return _apply_synonyms(normalize_text(text))


def _similarity(a: str, b: str) -> float:
    """Best of ordered and order-insensitive similarity, 0-100."""
    if not a or not b:
        return 0.0
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))


def _fuzzy_field(
    *,
    field: str,
    label: str,
    application_value: str,
    extracted: ExtractedField,
    review_at: float,
    normalizer=normalize_text,
    required: bool = True,
    wrapped_is_review: bool = False,
) -> FieldResult:
    """Compare one free-text field after normalization. With ``wrapped_is_review`` a value
    that appears whole inside the other ("Gin" in "Blood Orange Forward Gin", "Sounds" in
    "Sounds Vineyard") is a review item rather than a mismatch: the words are there, and
    whether the extra ones change the meaning is a person's call."""
    base = dict(
        field=field,
        label=label,
        application_value=application_value,
        label_value=extracted.value,
        confidence=extracted.confidence,
    )
    app_norm = normalizer(application_value)
    label_norm = normalizer(extracted.value)

    if not app_norm and not label_norm:
        return FieldResult(
            verdict=Verdict.NOT_APPLICABLE,
            reason="Not provided on the application or the label.",
            **base,
        )
    if not app_norm:
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW,
            reason=f"The label shows '{extracted.value}' but the application left this blank.",
            **base,
        )
    if not label_norm:
        verdict = Verdict.MISMATCH if required else Verdict.NEEDS_REVIEW
        return FieldResult(verdict=verdict, reason="Not found on the label.", **base)

    score = _similarity(app_norm, label_norm)
    if app_norm == label_norm:
        verdict = Verdict.MATCH
        if application_value.strip() == (extracted.value or "").strip():
            reason = "Matches the application."
        else:
            reason = "Matches the application after normalization (capitalization, punctuation, spacing, or standard abbreviations such as CA for California)."
    elif score >= review_at:
        verdict = Verdict.NEEDS_REVIEW
        reason = f"Similar but not identical to the application ({score:.0f}% similar). Confirm visually."
    elif wrapped_is_review and _wraps(app_norm, label_norm):
        verdict = Verdict.NEEDS_REVIEW
        reason = (
            f"The label reads '{extracted.value}' and the application says "
            f"'{application_value}': one is the other with words added ({score:.0f}% similar). "
            f"Confirm the added words are descriptive, not a different {label.lower()}."
        )
    else:
        verdict = Verdict.MISMATCH
        reason = f"Does not match the application ({score:.0f}% similar)."
    return FieldResult(verdict=verdict, reason=reason, similarity=round(score, 1), **base)


def _wraps(a: str, b: str) -> bool:
    """True when either normalized value contains the other as whole words."""
    short, long = sorted((a, b), key=len)
    return bool(short) and re.search(rf"\b{re.escape(short)}\b", long) is not None


def compare_brand_name(application: ApplicationData, extraction: LabelExtraction) -> FieldResult:
    result = _fuzzy_field(
        field="brand_name",
        label="Brand Name",
        application_value=application.brand_name,
        extracted=extraction.brand_name,
        review_at=BRAND_REVIEW,
        wrapped_is_review=True,
    )
    # The label carries two names and the reader took the other one for the brand: a
    # brewery name over a product name, or the reverse. Which is the brand is a person's
    # call (27 CFR 5.63, 4.33, 7.63), so this is a review item, not a mismatch.
    if result.verdict is Verdict.MISMATCH:
        app_norm = normalize_text(application.brand_name)
        other_norm = normalize_text(extraction.fanciful_name.value)
        if other_norm and (
            app_norm == other_norm or _similarity(app_norm, other_norm) >= BRAND_REVIEW
        ):
            result.verdict = Verdict.NEEDS_REVIEW
            result.reason = (
                f"The application's brand '{application.brand_name}' is on the label as a "
                f"second name ('{extraction.fanciful_name.value}'); the most prominent name "
                f"reads as '{extraction.brand_name.value}'. Confirm which is the brand name."
            )
    return result


def compare_class_type(application: ApplicationData, extraction: LabelExtraction) -> FieldResult:
    result = _fuzzy_field(
        field="class_type",
        label="Class / Type",
        application_value=application.class_type,
        extracted=extraction.class_type,
        review_at=CLASS_REVIEW,
        normalizer=_normalize_class,
        wrapped_is_review=True,  # "Gin" filed, "Blood Orange Forward Gin" printed
    )
    # A malt beverage must use a recognized class designation (27 CFR 7.64): beer, ale,
    # lager, stout, porter, malt liquor ... A designation with none of them goes to review.
    rules = _class_of(application, extraction)
    designation = normalize_text(extraction.class_type.value)
    if (
        rules.beverage_type is BeverageType.MALT_BEVERAGE
        and designation
        and not _MALT_DESIGNATION_RE.search(designation)
    ):
        result.notes.append(
            f"'{extraction.class_type.value}' contains no recognized class designation (beer, "
            f"ale, lager, stout, porter, malt liquor ...) ({rules.rule('class_type').citation})."
        )
        if result.verdict is Verdict.MATCH:
            result.verdict = Verdict.NEEDS_REVIEW
    return result


# Words in a class/type designation that settle which TTB category the product is in.
# Spirits are checked first so "Single Malt Scotch Whisky" is not read as a malt beverage,
# and "rye" counts as a spirit only next to "whiskey"; "Rye Ale" is beer.
_CATEGORY_PATTERNS: tuple[tuple[BeverageType, re.Pattern[str]], ...] = (
    (
        BeverageType.DISTILLED_SPIRITS,
        re.compile(
            r"\b(whisk(e)?y|bourbon|scotch|vodka|gin|rum|tequila|mezcal|brandy|cognac|"
            r"armagnac|liqueur|cordial|absinthe|schnapps|moonshine|grappa|pisco|"
            r"distilled spirits?|spirits?|rye whisk(e)?y|straight rye)\b"
        ),
    ),
    (
        BeverageType.WINE,
        re.compile(
            r"\b(wine|cabernet|chardonnay|merlot|pinot|sauvignon|riesling|ros[eé]|champagne|"
            r"sparkling|prosecco|zinfandel|syrah|shiraz|malbec|sangria|vermouth|port|sherry|"
            r"cider|mead|moscato|tempranillo|grenache|sangiovese)\b"
        ),
    ),
    (
        BeverageType.MALT_BEVERAGE,
        re.compile(
            r"\b(beer|ale|lager|ipa|india pale|stout|porter|pilsner|pils|hefeweizen|witbier|"
            r"saison|lambic|k[oö]lsch|bock|malt beverage|malt liquor|hard seltzer)\b"
        ),
    ),
)

_MALT_DESIGNATION_RE = dict(_CATEGORY_PATTERNS)[BeverageType.MALT_BEVERAGE]

CATEGORY_LABELS = {
    BeverageType.DISTILLED_SPIRITS: "distilled spirits",
    BeverageType.WINE: "wine",
    BeverageType.MALT_BEVERAGE: "a malt beverage",
}
# The three commodity classes as they appear on the form and in the comparison table.
CATEGORY_NAMES = {
    BeverageType.DISTILLED_SPIRITS: "Distilled spirits",
    BeverageType.WINE: "Wine",
    BeverageType.MALT_BEVERAGE: "Malt beverage",
}


def infer_beverage_category(class_type: str | None) -> BeverageType | None:
    """Which category a class/type designation belongs to, or None when it does not say."""
    text = normalize_text(class_type)
    if not text:
        return None
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text):
            return category
    return None


def resolve_beverage_type(
    application: ApplicationData, extraction: LabelExtraction
) -> tuple[BeverageType | None, str]:
    """The class whose rules apply, and where it came from: 'filed', 'class_type' (the
    designation's words), 'reader' (the model's judgment of the whole label), or 'unknown'."""
    if application.beverage_type is not None:
        return BeverageType(application.beverage_type), "filed"
    implied = infer_beverage_category(extraction.class_type.value)
    if implied is not None:
        return implied, "class_type"
    guess = extraction.product_category
    if guess.value in {b.value for b in BeverageType} and guess.confidence >= CATEGORY_CONFIDENCE:
        return BeverageType(guess.value), "reader"
    return None, "unknown"


# The rulebook for the verify() call in progress, so a dozen comparators do not each
# re-resolve the class. Context-local: batch worker threads each see their own.
_CURRENT_RULES: ContextVar[ClassRules | None] = ContextVar("labelverify_rules", default=None)


def _class_of(application: ApplicationData, extraction: LabelExtraction) -> ClassRules:
    """The rulebook to apply. An unresolved class is checked as distilled spirits, the
    strictest of the three, and the type-of-product row says so."""
    current = _CURRENT_RULES.get()
    if current is not None:
        return current
    resolved, _ = resolve_beverage_type(application, extraction)
    return rules_for(resolved or BeverageType.DISTILLED_SPIRITS)


def compare_beverage_type(application: ApplicationData, extraction: LabelExtraction) -> FieldResult:
    """The type of product on the application against what the label's class/type implies.

    Filing a bourbon as wine is an application error the other fields cannot catch, since
    the class/type text itself may match perfectly. Only unambiguous words decide; a
    designation that names no category is left to the specialist as not applicable.
    """
    designation = (extraction.class_type.value or "").strip()
    implied = infer_beverage_category(designation)
    if implied is None:
        guess = extraction.product_category
        if (
            guess.value in {b.value for b in BeverageType}
            and guess.confidence >= CATEGORY_CONFIDENCE
        ):
            implied = BeverageType(guess.value)
    confidence = (
        extraction.class_type.confidence if implied else extraction.product_category.confidence
    )
    base = dict(
        field="beverage_type",
        label="Type of Product",
        label_value=CATEGORY_NAMES[implied] if implied else "Not stated on the label",
        confidence=confidence,
        citation="27 CFR parts 4, 5, and 7",
    )

    if application.beverage_type is None:  # not filed: the label decides
        if implied is None:
            return FieldResult(
                verdict=Verdict.NEEDS_REVIEW,
                application_value="Not filed",
                reason=(
                    "The application did not state a type and the label does not say which "
                    "of the three classes the product is in. Checked under the distilled "
                    "spirits rules, the strictest; a specialist should confirm the class."
                ),
                **base,
            )
        return FieldResult(
            verdict=Verdict.NOT_APPLICABLE,
            application_value="Not filed",
            reason=(
                f"The application did not state a type. The label reads as "
                f"{CATEGORY_LABELS[implied]} and the {rules_for(implied).name.lower()} rules "
                f"(27 CFR part {rules_for(implied).part}) were applied."
            ),
            **base,
        )

    filed = BeverageType(application.beverage_type)
    base["application_value"] = CATEGORY_NAMES[filed]
    if implied is None:
        return FieldResult(
            verdict=Verdict.NOT_APPLICABLE,
            reason=(
                f"The class/type on the label ('{designation}') does not say which of the "
                "three categories the product is in."
                if designation
                else "No class/type was read from the label, so the category cannot be checked."
            ),
            **base,
        )
    if implied is filed:
        return FieldResult(
            verdict=Verdict.MATCH,
            reason=f"The label reads as {CATEGORY_LABELS[implied]} ('{designation}'), as filed.",
            **base,
        )
    return FieldResult(
        verdict=Verdict.MISMATCH,
        reason=(
            f"The label reads as {CATEGORY_LABELS[implied]} ('{designation}') "
            f"but the application is filed as {CATEGORY_LABELS[filed]}."
        ),
        **base,
    )


_VERDICT_RANK = {
    Verdict.MATCH: 0,
    Verdict.NOT_APPLICABLE: 1,
    Verdict.NEEDS_REVIEW: 2,
    Verdict.MISMATCH: 3,
}


def compare_producer_name(application: ApplicationData, extraction: LabelExtraction) -> FieldResult:
    """Entity suffixes are ignored ("Vinovae, Inc." is "Vinovae"), and a filing that lists
    more than one name ("Go Brewing, Go Brewing Opco, LLC": trade name, legal name) matches
    when any of them is the name printed."""
    results = [
        _fuzzy_field(
            field="producer_name",
            label="Producer / Bottler Name",
            application_value=application.producer_name,
            extracted=extraction.producer_name,
            review_at=PRODUCER_REVIEW,
            normalizer=normalize_producer,
            wrapped_is_review=True,  # "SVP Winery" filed, "SVP Winery, LLC" printed
        )
    ]
    for candidate in producer_candidates(application.producer_name)[1:]:
        part = _fuzzy_field(
            field="producer_name",
            label="Producer / Bottler Name",
            application_value=candidate,
            extracted=extraction.producer_name,
            review_at=PRODUCER_REVIEW,
            normalizer=normalize_producer,
            wrapped_is_review=True,
        )
        if _VERDICT_RANK[part.verdict] < _VERDICT_RANK[results[0].verdict]:
            part.application_value = application.producer_name
            part.reason = (
                f"'{extraction.producer_name.value}' matches '{candidate}', one of the names the "
                f"application lists. {part.reason}"
            )
            results.insert(0, part)
    return results[0]


def compare_producer_address(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult:
    result = _fuzzy_field(
        field="producer_address",
        label="Producer / Bottler Address",
        application_value=application.producer_address,
        extracted=extraction.producer_address,
        review_at=ADDRESS_REVIEW,
        normalizer=normalize_address,
        required=False,
    )
    # The label need only carry the city and state (27 CFR 5.66, 4.35, 7.66). An
    # application that gives the street address agrees with a label that prints
    # "Lexington, KY" when every word the label prints is in the application's address.
    if result.verdict in (Verdict.NEEDS_REVIEW, Verdict.MISMATCH):
        label_words = normalize_address(extraction.producer_address.value).split()
        app_words = set(normalize_address(application.producer_address).split())
        if len(label_words) >= 2 and all(w in app_words for w in label_words):
            result.verdict = Verdict.MATCH
            result.reason = (
                f"The label states '{extraction.producer_address.value}', the city and state "
                "the rule requires; the application's full address contains them."
            )
    return result


def compare_alcohol_content(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult:
    """Alcohol content under the class's rules.

    Spirits and wine must state it; malt beverages need not (27 CFR 7.65). Wine between 7
    and 14 percent may print 'Table Wine' or 'Light Wine' instead of a number (27 CFR 4.36).
    Identical values match. A difference inside the class's labeling tolerance goes to
    review, since the application should carry the labeled figure; beyond it is a mismatch.
    """
    rules = _class_of(application, extraction)
    extracted = extraction.alcohol_content
    base = dict(
        field="alcohol_content",
        label="Alcohol Content",
        application_value=application.alcohol_content,
        label_value=extracted.value,
        confidence=extracted.confidence,
    )
    app_abv = parse_alcohol_content(application.alcohol_content)
    label_abv = parse_alcohol_content(extracted.value)
    label_text = (extracted.value or "").strip()
    optional = rules.requirement("alcohol_content") is Requirement.OPTIONAL

    if label_abv is None and not label_text:  # nothing printed
        if optional:
            note = (
                f"The application states {app_abv.abv:g}%; printing it is not required."
                if app_abv
                else ""
            )
            return FieldResult(
                verdict=Verdict.NOT_APPLICABLE,
                reason=(
                    f"Not printed on the label, which {rules.rule('alcohol_content').citation} "
                    "allows for malt beverages at the federal level; some states require it."
                ),
                notes=[note] if note else [],
                **base,
            )
        if rules.table_wine_exemption and (app_abv is None or 7.0 <= app_abv.abv <= 14.0):
            designation = normalize_text(extraction.class_type.value)
            if "table wine" in designation or "light wine" in designation:
                stated = f"; the application states {app_abv.abv:g}%" if app_abv else ""
                return FieldResult(
                    verdict=Verdict.MATCH,
                    reason=(
                        f"The label says '{extraction.class_type.value}' instead of a number, "
                        f"which {rules.rule('alcohol_content').citation} allows between 7 and "
                        f"14 percent{stated}."
                    ),
                    **base,
                )
        if app_abv is None and not application.alcohol_content.strip():
            return FieldResult(
                verdict=Verdict.MISMATCH,
                reason=f"Alcohol content is required ({rules.rule('alcohol_content').citation}) and was not found on the label or the application.",
                **base,
            )
        return FieldResult(
            verdict=Verdict.MISMATCH,
            reason=(
                f"Application states {app_abv.abv:g}% but no alcohol content was found on the "
                f"label; it is required ({rules.rule('alcohol_content').citation})."
                if app_abv
                else f"No alcohol content was found on the label; it is required ({rules.rule('alcohol_content').citation})."
            ),
            **base,
        )
    if label_abv is None:
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW,
            reason=f"The label shows '{extracted.value}', which could not be read as a percentage or proof. Confirm visually.",
            **base,
        )
    if app_abv is None:
        if application.alcohol_content.strip():
            reason = (
                f"The label shows '{extracted.value}' but the application value could not be read."
            )
        elif optional:
            return FieldResult(
                verdict=Verdict.NEEDS_REVIEW,
                reason=f"The label states {label_abv.abv:g}% but the application left it blank. Add it to the application.",
                **base,
            )
        else:
            reason = f"The label states {label_abv.abv:g}% but the application left it blank."
        return FieldResult(verdict=Verdict.NEEDS_REVIEW, reason=reason, **base)

    notes: list[str] = []
    if label_abv.source == "percent" and not _ALC_WORDS_RE.search(label_text):
        notes.append(
            f"The label shows '{label_text}' without the words 'alcohol by volume' or an "
            f"abbreviation such as 'Alc./Vol.' or 'ABV' ({rules.rule('alcohol_content').citation})."
        )
    if label_abv.proof is not None:
        if not rules.proof_permitted:
            notes.append(
                f"Proof is a distilled spirits convention; {rules.name.lower()} labels state percent alcohol by volume."
            )
        elif label_abv.source == "percent" and abs(label_abv.proof - 2 * label_abv.abv) > 0.1:
            notes.append(
                f"Label proof ({label_abv.proof:g}) does not equal twice the stated percentage."
            )
    difference = abs(app_abv.abv - label_abv.abv)
    if any("does not equal twice" in n for n in notes):
        return FieldResult(
            verdict=Verdict.MISMATCH,
            reason="The proof printed on the label does not agree with its own percentage.",
            notes=notes,
            **base,
        )
    if difference <= ABV_TOLERANCE:
        verdict = Verdict.NEEDS_REVIEW if notes else Verdict.MATCH
        reason = f"Both state {label_abv.abv:g}% alcohol by volume."
    elif difference <= rules.abv_tolerance(label_abv.abv):
        line = _wine_tax_class_line(rules, app_abv.abv, label_abv.abv)
        if line is not None:
            verdict = Verdict.MISMATCH
            reason = (
                f"Application states {app_abv.abv:g}% but the label shows {label_abv.abv:g}%: "
                f"the two fall on different sides of the {line:g} percent tax class line, which "
                f"the ±{rules.abv_tolerance(label_abv.abv):g} tolerance "
                f"({rules.rule('alcohol_content').citation}) does not bridge."
            )
        else:
            verdict = Verdict.NEEDS_REVIEW
            reason = (
                f"Application states {app_abv.abv:g}% but the label shows {label_abv.abv:g}%. The "
                f"difference is within the ±{rules.abv_tolerance(label_abv.abv):g} labeling tolerance "
                f"({rules.rule('alcohol_content').citation}), but the application should carry the labeled figure."
            )
    else:
        verdict = Verdict.MISMATCH
        reason = f"Application states {app_abv.abv:g}% but the label shows {label_abv.abv:g}%."
    return FieldResult(verdict=verdict, reason=reason, notes=notes, **base)


_ALC_WORDS_RE = re.compile(r"alc|abv|alcohol", re.IGNORECASE)

# Wine is taxed in classes divided at these percentages; a labeling tolerance that
# straddled one would change the tax class, so it is not allowed to.
_WINE_TAX_CLASS_LINES = (14.0, 21.0, 24.0)


def _wine_tax_class_line(rules: ClassRules, filed: float, labeled: float) -> float | None:
    """The tax class line, if any, that the filed and labeled percentages straddle."""
    if rules.beverage_type is not BeverageType.WINE:
        return None
    for line in _WINE_TAX_CLASS_LINES:
        if (filed <= line) != (labeled <= line):
            return line
    return None


def compare_net_contents(application: ApplicationData, extraction: LabelExtraction) -> FieldResult:
    extracted = extraction.net_contents
    base = dict(
        field="net_contents",
        label="Net Contents",
        application_value=application.net_contents,
        label_value=extracted.value,
        confidence=extracted.confidence,
    )
    app_vol = parse_net_contents(application.net_contents)
    label_vol = parse_net_contents(extracted.value)
    rules = _class_of(application, extraction)
    # On a malt beverage the statement may be blown into the glass rather than printed
    # (27 CFR 7.70), so a label without one is a review item, not a finding.
    on_container = rules.beverage_type is BeverageType.MALT_BEVERAGE
    missing_note = (
        f" On a malt beverage it may be blown into the glass instead "
        f"({rules.rule('net_contents').citation}); confirm on the container."
        if on_container
        else ""
    )

    if app_vol is None and label_vol is None:
        if application.net_contents.strip() or (extracted.value or "").strip():
            reason = "Could not read a volume. Confirm visually."
            return FieldResult(verdict=Verdict.NEEDS_REVIEW, reason=reason, **base)
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW if on_container else Verdict.MISMATCH,
            reason="Net contents are required and were not found." + missing_note,
            **base,
        )
    if app_vol is None:
        reason = (
            f"The label shows '{extracted.value}' but the application value could not be read."
            if application.net_contents.strip()
            else f"The label shows '{extracted.value}' but the application left it blank."
        )
        return FieldResult(verdict=Verdict.NEEDS_REVIEW, reason=reason, **base)
    if label_vol is None:
        if (extracted.value or "").strip():
            return FieldResult(
                verdict=Verdict.NEEDS_REVIEW,
                reason=f"The label shows '{extracted.value}', which could not be read as a volume. Confirm visually.",
                **base,
            )
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW if on_container else Verdict.MISMATCH,
            reason=f"Application states {_fmt_ml(app_vol.milliliters)} but no net contents were found on the label."
            + missing_note,
            **base,
        )

    notes = _standard_of_fill_notes(rules, label_vol.milliliters)
    metric_missing = rules.metric_required and label_vol.original_unit not in METRIC_UNITS
    if metric_missing:
        notes.append(
            f"Only '{extracted.value}' was found; {rules.name.lower()} labels must state net "
            f"contents in metric units ({rules.rule('net_contents').citation})."
        )
    if abs(app_vol.milliliters - label_vol.milliliters) <= _volume_tolerance(app_vol.milliliters):
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW if metric_missing else Verdict.MATCH,
            reason=f"Both state {_fmt_ml(label_vol.milliliters)}."
            + (" The label lacks the required metric statement." if metric_missing else ""),
            notes=notes,
            **base,
        )
    return FieldResult(
        verdict=Verdict.MISMATCH,
        reason=(
            f"Application states {_fmt_ml(app_vol.milliliters)} but the label shows "
            f"{_fmt_ml(label_vol.milliliters)}."
        ),
        notes=notes,
        **base,
    )


def _volume_tolerance(ml: float) -> float:
    """Reading slack for volumes: a millilitre, or half a percent when the two statements
    use different units (25.4 fl oz is 751 mL)."""
    return max(VOLUME_TOLERANCE_ML, ml * 0.005)


def _fmt_ml(ml: float) -> str:
    return f"{ml:g} mL"


def _standard_of_fill_notes(rules: ClassRules, ml: float) -> list[str]:
    standards = rules.standards_of_fill_ml
    if standards is None:
        return []
    if any(abs(ml - s) <= VOLUME_TOLERANCE_ML for s in standards):
        return []
    return [f"{_fmt_ml(ml)} is not a listed standard of fill for {rules.name.lower()}. Confirm."]


def compare_sulfite_declaration(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult:
    """Wine only: 'Contains sulfites' is required at 10 ppm or more (27 CFR 4.32(e)). The
    label cannot show the sulfur dioxide level, so a missing statement is a review item,
    not a failure: the wine may qualify for the exemption."""
    extracted = extraction.sulfite_declaration
    base = dict(
        field="sulfite_declaration",
        label="Sulfite Declaration",
        application_value="Required at 10 ppm or more",
        label_value=extracted.value,
        confidence=extracted.confidence,
    )
    if (extracted.value or "").strip():
        return FieldResult(
            verdict=Verdict.MATCH,
            reason=f"The label declares sulfites ('{extracted.value}').",
            **base,
        )
    return FieldResult(
        verdict=Verdict.NEEDS_REVIEW,
        reason=(
            "No sulfite declaration was found. It is required when sulfur dioxide is 10 ppm "
            "or more; confirm the wine qualifies for the exemption or add the statement."
        ),
        **base,
    )


def compare_country_of_origin(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult:
    extracted = extraction.country_of_origin
    base = dict(
        field="country_of_origin",
        label="Country of Origin",
        application_value=application.country_of_origin,
        label_value=extracted.value,
        confidence=extracted.confidence,
    )
    app_country = normalize_country(application.country_of_origin)
    label_country = normalize_country(extracted.value)

    if not application.is_import:
        if label_country and app_country and label_country != app_country:
            return FieldResult(
                verdict=Verdict.NEEDS_REVIEW,
                reason="Application is not marked as an import, but the label and application list different countries.",
                **base,
            )
        return FieldResult(
            verdict=Verdict.NOT_APPLICABLE,
            reason="Country of origin is only required for imported products.",
            **base,
        )
    if not app_country:
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW,
            reason="Application is marked as an import but no country of origin was entered.",
            **base,
        )
    if not label_country:
        return FieldResult(
            verdict=Verdict.MISMATCH,
            reason="Imported products must show the country of origin, and none was found on the label.",
            **base,
        )
    if app_country == label_country:
        return FieldResult(verdict=Verdict.MATCH, reason="Matches the application.", **base)
    return FieldResult(
        verdict=Verdict.MISMATCH,
        reason=f"Application states '{application.country_of_origin}' but the label shows '{extracted.value}'.",
        **base,
    )


def read_is_uncertain(extraction: LabelExtraction) -> bool:
    """True when nothing in the read reached ordinary confidence: an OCR read, or a model
    read of an image it could barely make out. Absence from such a read is not evidence of
    absence from the label."""
    confidences = [
        getattr(extraction, name).confidence
        for name, field in LabelExtraction.model_fields.items()
        if field.annotation is ExtractedField and getattr(extraction, name).value
    ]
    if extraction.health_warning.present:
        confidences.append(extraction.health_warning.confidence)
    if not confidences:  # nothing read at all: the most uncertain read there is
        return True
    return max(confidences) <= LOW_CONFIDENCE


def _soften_absences(fields: list[FieldResult]) -> list[str]:
    """On an uncertain read, a required item the reader did not find is a review item,
    not a finding: the reader misses text far more often than labels omit it. Real
    differences in what was read stay mismatches. Returns the labels softened."""
    softened = []
    for f in fields:
        if f.verdict is not Verdict.MISMATCH:
            continue
        absent = not (f.label_value or "").strip()
        # A warning transcription a word or two off is the usual OCR slip; the heading's
        # capitalization is read reliably and stays a finding.
        slip = (
            f.field == "health_warning"
            and f.diff
            and sum(1 for d in f.diff if d.op != "equal") <= 3
            and "capital letters" not in f.reason
        )
        if absent or slip:
            f.verdict = Verdict.NEEDS_REVIEW
            f.uncertain = True
            f.reason += (
                " The reader was uncertain, so confirm on the image before treating this as "
                + ("missing." if absent else "a wording difference.")
            )
            softened.append(f.label)
    return softened


def _apply_confidence_gate(result: FieldResult) -> FieldResult:
    """A match from a low-confidence read is never an unattended match.

    The row keeps the verdict the comparison found (the values do agree) and is marked
    uncertain, which the roll-up treats as a review item. Showing "match, unverified
    read" rather than "review" keeps the table honest about what was compared and what
    was merely read badly, which matters most on an OCR read where every row is uncertain.
    """
    if result.verdict is Verdict.MATCH and result.confidence < LOW_CONFIDENCE:
        result.uncertain = True
        result.notes.append(
            f"Low read confidence ({result.confidence:.0%}). Confirm the label text visually."
        )
    elif (
        result.verdict is Verdict.MISMATCH
        and (result.label_value or "").strip()
        and result.confidence < LOW_CONFIDENCE
    ):
        # A difference the reader itself is unsure of is not a finding yet. TTB's practice
        # for a label that cannot be read is to ask for a better image, not to reject it
        # for a mismatch, so the row asks for that instead of charging the label.
        result.uncertain = True
        result.verdict = Verdict.NEEDS_REVIEW
        result.notes.append(
            f"Read at {result.confidence:.0%} confidence, so this may be the reader rather "
            "than the label. Confirm on the image, or ask for a clearer photo."
        )
    return result


_COMPARATORS = (
    compare_brand_name,
    compare_class_type,
    compare_beverage_type,
    compare_alcohol_content,
    compare_net_contents,
    compare_producer_name,
    compare_producer_address,
    compare_country_of_origin,
)

# --------------------------------------------------------------------------- class-specific rows
#
# These rows appear only when the class's rulebook has the rule and the label gives the
# engine something to check. All but the bottled-in-bond proof are review items: the engine
# can see that a statement is missing, not whether an exemption applies.

_WHISKY_RE = re.compile(r"\b(whisk(e)?y|bourbon|rye|scotch|malt)\b")


def compare_qualifying_phrase(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult:
    """The words before the producer's name (27 CFR 5.66, 4.35, 7.66); imports must also
    name the importer."""
    rules = _class_of(application, extraction)
    extracted = extraction.qualifying_phrase
    phrase = (extracted.value or "").strip()
    base = dict(
        field="qualifying_phrase",
        label="Qualifying Phrase",
        application_value=", ".join(rules.qualifying_phrases[:2]) + ", or similar",
        label_value=phrase or None,
        confidence=extracted.confidence,
    )
    if not phrase:
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW,
            reason=(
                "No qualifying phrase such as "
                f"'{rules.qualifying_phrases[0]}' was read before the producer's name; "
                f"one is required ({rules.rule('qualifying_phrase').citation})."
            ),
            **base,
        )
    if application.is_import:
        importer = (extraction.importer_statement.value or "").strip()
        if (
            not importer
            and "import" not in f"{phrase} {extraction.producer_name.value or ''}".lower()
        ):
            return FieldResult(
                verdict=Verdict.NEEDS_REVIEW,
                reason=(
                    f"The label says '{phrase}', but an imported product must also name the "
                    f"U.S. importer ('Imported by ...'); none was read."
                ),
                **base,
            )
    return FieldResult(verdict=Verdict.MATCH, reason=f"The label says '{phrase}'.", **base)


def _is_whisky(extraction: LabelExtraction) -> bool:
    return bool(_WHISKY_RE.search(normalize_text(extraction.class_type.value)))


def compare_age_statement(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult | None:
    """Whisky only: a statement of age is required when the whisky is under four years old,
    so a whisky label without one goes to review (27 CFR 5.141)."""
    rules = _class_of(application, extraction)
    if rules.requirement("age_statement") is Requirement.NOT_APPLICABLE or not _is_whisky(
        extraction
    ):
        return None
    extracted = extraction.age_statement
    statement = (extracted.value or "").strip()
    base = dict(
        field="age_statement",
        label="Age Statement",
        application_value="Required if aged under 4 years",
        label_value=statement or None,
        confidence=extracted.confidence,
    )
    if statement:
        return FieldResult(verdict=Verdict.MATCH, reason=f"The label states '{statement}'.", **base)
    return FieldResult(
        verdict=Verdict.NEEDS_REVIEW,
        reason=(
            "No age statement was read. Whisky aged under four years must state its age "
            f"({rules.rule('age_statement').citation}); confirm the age or add the statement."
        ),
        **base,
    )


def compare_bottled_in_bond(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult | None:
    """Only when the label claims 'Bottled in Bond': the claim requires 100 proof."""
    rules = _class_of(application, extraction)
    claim = (extraction.bottled_in_bond_claim.value or "").strip()
    if rules.requirement("bottled_in_bond") is Requirement.NOT_APPLICABLE or not claim:
        return None
    abv = parse_alcohol_content(extraction.alcohol_content.value)
    base = dict(
        field="bottled_in_bond",
        label="Bottled in Bond",
        application_value="100 proof (50% alc/vol)",
        label_value=(
            f"{claim} · {extraction.alcohol_content.value}"
            if extraction.alcohol_content.value
            else claim
        ),
        confidence=min(
            extraction.bottled_in_bond_claim.confidence, extraction.alcohol_content.confidence
        ),
    )
    if abv is None:
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW,
            reason=f"The label claims '{claim}' but its alcohol content could not be read to confirm 100 proof.",
            **base,
        )
    if abs(abv.abv - 50.0) <= ABV_TOLERANCE:
        return FieldResult(
            verdict=Verdict.MATCH,
            reason=f"'{claim}' at {abv.abv:g}% alcohol by volume, as required.",
            **base,
        )
    return FieldResult(
        verdict=Verdict.MISMATCH,
        reason=(
            f"The label claims '{claim}' but states {abv.abv:g}% alcohol by volume; bottled-in-bond "
            f"spirits must be 100 proof ({rules.rule('bottled_in_bond').citation})."
        ),
        **base,
    )


def compare_blend_percentage(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult | None:
    """Only when the class/type says blended: the percentage of straight whisky must appear."""
    rules = _class_of(application, extraction)
    designation = normalize_text(extraction.class_type.value)
    if (
        rules.requirement("blend_percentage") is Requirement.NOT_APPLICABLE
        or "blend" not in designation
    ):
        return None
    extracted = extraction.blend_percentage
    statement = (extracted.value or "").strip()
    base = dict(
        field="blend_percentage",
        label="Blend Percentage",
        application_value="Required for blends",
        label_value=statement or None,
        confidence=extracted.confidence,
    )
    if statement:
        return FieldResult(verdict=Verdict.MATCH, reason=f"The label states '{statement}'.", **base)
    return FieldResult(
        verdict=Verdict.NEEDS_REVIEW,
        reason=(
            f"The class/type is a blend ('{extraction.class_type.value}') but no percentage "
            f"statement was read; one is required ({rules.rule('blend_percentage').citation})."
        ),
        **base,
    )


_YEAR_RE = re.compile(r"\b((?:18|19|20)\d{2})\b")
_RECORDS_NOTE = "The grape-source percentages behind the statement are checked from records."


def _wine_rule(application: ApplicationData, extraction: LabelExtraction, field: str):
    """The class's rule for ``field``, or None when the class does not carry the rule."""
    rules = _class_of(application, extraction)
    if rules.requirement(field) is Requirement.NOT_APPLICABLE:
        return None
    return rules


def compare_appellation(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult | None:
    """Wine only, when the label names an origin: shown with its citation. Whether enough
    of the grapes came from there (27 CFR 4.25) is a records check, so the row informs."""
    rules = _wine_rule(application, extraction, "appellation")
    name = (extraction.appellation.value or "").strip()
    if rules is None or not name:
        return None
    return FieldResult(
        verdict=Verdict.MATCH,
        reason=f"The label names '{name}' as the appellation of origin.",
        notes=[_RECORDS_NOTE],
        field="appellation",
        label="Appellation of Origin",
        application_value="Named origin (75% of grapes; 85% for a viticultural area)",
        label_value=name,
        confidence=extraction.appellation.confidence,
    )


def compare_vintage_year(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult | None:
    """Wine only, when the label states a vintage: a vintage date needs an appellation of
    origin beside it (27 CFR 4.27), and the year must be a real past year."""
    rules = _wine_rule(application, extraction, "vintage_year")
    stated = (extraction.vintage_year.value or "").strip()
    if rules is None or not stated:
        return None
    citation = rules.rule("vintage_year").citation
    base = dict(
        field="vintage_year",
        label="Vintage Year",
        application_value="Requires an appellation of origin",
        label_value=stated,
        confidence=min(extraction.vintage_year.confidence, extraction.appellation.confidence)
        if (extraction.appellation.value or "").strip()
        else extraction.vintage_year.confidence,
    )
    match = _YEAR_RE.search(stated)
    if not match:
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW,
            reason=f"'{stated}' could not be read as a year. Confirm visually.",
            **base,
        )
    year = int(match.group(1))
    if year > date.today().year:
        return FieldResult(
            verdict=Verdict.MISMATCH,
            reason=f"The label states a {year} vintage, which has not happened yet.",
            **base,
        )
    appellation = (extraction.appellation.value or "").strip()
    if not appellation:
        return FieldResult(
            verdict=Verdict.MISMATCH,
            reason=(
                f"The label states a {year} vintage but names no appellation of origin; a "
                f"vintage date may be used only with one ({citation})."
            ),
            **base,
        )
    return FieldResult(
        verdict=Verdict.MATCH,
        reason=f"The {year} vintage is paired with the '{appellation}' appellation, as {citation} requires.",
        notes=[_RECORDS_NOTE],
        **base,
    )


def compare_estate_bottled(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult | None:
    """Wine only, when the label claims 'Estate Bottled': the claim needs a viticultural
    area appellation on the label (27 CFR 4.26); the rest is a records check."""
    rules = _wine_rule(application, extraction, "estate_bottled")
    claim = (extraction.estate_bottled_claim.value or "").strip()
    if rules is None or not claim:
        return None
    citation = rules.rule("estate_bottled").citation
    appellation = (extraction.appellation.value or "").strip()
    base = dict(
        field="estate_bottled",
        label="Estate Bottled",
        application_value="Requires a viticultural area appellation",
        label_value=f"{claim} · {appellation}" if appellation else claim,
        confidence=extraction.estate_bottled_claim.confidence,
    )
    if not appellation:
        return FieldResult(
            verdict=Verdict.MISMATCH,
            reason=(
                f"The label claims '{claim}' but names no appellation of origin; the claim "
                f"requires a viticultural area appellation ({citation})."
            ),
            **base,
        )
    return FieldResult(
        verdict=Verdict.MATCH,
        reason=f"'{claim}' appears with the '{appellation}' appellation.",
        notes=[
            f"Confirm '{appellation}' is a viticultural area and that the winery grew, made, "
            f"and bottled the wine within it ({citation}); both are records checks."
        ],
        **base,
    )


_STRENGTH_RE = re.compile(
    r"\b(extra[\s-]+strength|full[\s-]+strength|high[\s-]+test|high[\s-]+proof|"
    r"pre[\s-]*war[\s-]+strength|full old[\s-]+time alcoholic strength|strong)\b",
    re.IGNORECASE,
)


def compare_strength_claim(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult | None:
    """Malt beverages only: wording that emphasizes alcoholic strength is not permitted
    (27 CFR 7.65). The reader reports such wording; the brand, class, and alcohol
    statement are scanned as well. A row appears only when something was found."""
    rules = _wine_rule(application, extraction, "strength_claim")
    if rules is None:
        return None
    claim = (extraction.strength_claim.value or "").strip()
    confidence = extraction.strength_claim.confidence
    if not claim:
        for part in (extraction.brand_name, extraction.class_type, extraction.alcohol_content):
            match = _STRENGTH_RE.search(part.value or "")
            if match:
                claim, confidence = match.group(1), part.confidence
                break
    if not claim:
        return None
    return FieldResult(
        verdict=Verdict.NEEDS_REVIEW,
        reason=(
            f"The label says '{claim}'. A malt beverage label may not emphasize alcoholic "
            f"strength ({rules.rule('strength_claim').citation}); 'strong' inside a recognized "
            "style name is a judgment for the specialist."
        ),
        field="strength_claim",
        label="Statement of Strength",
        application_value="Not permitted",
        label_value=claim,
        confidence=confidence,
    )


_CONDITIONAL_COMPARATORS = (
    compare_age_statement,
    compare_bottled_in_bond,
    compare_blend_percentage,
    compare_appellation,
    compare_vintage_year,
    compare_estate_bottled,
    compare_strength_claim,
)


def verify(application: ApplicationData, extraction: LabelExtraction) -> VerificationResult:
    """Compare every required field under the class's rules and roll the verdicts up."""
    resolved, source = resolve_beverage_type(application, extraction)
    rules = rules_for(resolved or BeverageType.DISTILLED_SPIRITS)
    token = _CURRENT_RULES.set(rules)
    try:
        return _verify_with(application, extraction, rules, resolved, source)
    finally:
        _CURRENT_RULES.reset(token)


def _verify_with(
    application: ApplicationData,
    extraction: LabelExtraction,
    rules: ClassRules,
    resolved: BeverageType | None,
    source: str,
) -> VerificationResult:
    fields = [_apply_confidence_gate(fn(application, extraction)) for fn in _COMPARATORS]
    fields.append(_apply_confidence_gate(compare_qualifying_phrase(application, extraction)))
    if rules.requirement("sulfite_declaration") is not Requirement.NOT_APPLICABLE:
        fields.append(_apply_confidence_gate(compare_sulfite_declaration(application, extraction)))
    for conditional in _CONDITIONAL_COMPARATORS:
        row = conditional(application, extraction)
        if row is not None:
            fields.append(_apply_confidence_gate(row))
    fields.append(_apply_confidence_gate(check_health_warning(extraction.health_warning)))
    softened = _soften_absences(fields) if read_is_uncertain(extraction) else []
    for f in fields:
        rule = rules.fields.get(f.field)
        if rule is not None:
            f.citation = f.citation or rule.citation
            f.requirement = rule.requirement.value

    summary: list[str] = []
    if not extraction.image_quality.readable:
        summary.append("The image could not be read reliably. Ask for a clearer photo.")
    for issue in extraction.image_quality.issues:
        summary.append(f"Image note: {issue}")
    if source in ("class_type", "reader"):
        summary.append(
            f"Type of product taken from the label: {rules.name.lower()} "
            f"(27 CFR part {rules.part})."
        )
    elif source == "unknown":
        summary.append(
            "Type of product could not be determined; checked under the distilled spirits rules."
        )

    verdicts = {f.verdict for f in fields}
    if Verdict.MISMATCH in verdicts:
        recommendation = Recommendation.REQUEST_CORRECTION
        for f in fields:
            if f.verdict is Verdict.MISMATCH:
                summary.append(f"{f.label}: {f.reason}")
    elif (
        Verdict.NEEDS_REVIEW in verdicts
        or any(f.uncertain for f in fields)
        or not extraction.image_quality.readable
    ):
        recommendation = Recommendation.NEEDS_REVIEW
        for f in fields:
            if f.verdict is Verdict.NEEDS_REVIEW:
                summary.append(f"{f.label}: {f.reason}")
        unsure = [
            f.label
            for f in fields
            if f.uncertain and f.label not in softened and f.verdict is Verdict.NEEDS_REVIEW
        ]
        agreeing = [f.label for f in fields if f.uncertain and f.verdict is Verdict.MATCH]
        if unsure:
            summary.append(
                f"{len(unsure)} difference{'s' if len(unsure) != 1 else ''} on a low-confidence "
                f"read ({', '.join(unsure)}): confirm on the image, or ask for a clearer photo."
            )
        if agreeing:
            summary.append(
                f"{len(agreeing)} matching field{'s' if len(agreeing) != 1 else ''} came from "
                f"a low-confidence read ({', '.join(agreeing)}); confirm against the image."
            )
    else:
        recommendation = Recommendation.APPROVE
        summary.append("All required fields match the application.")
    if softened:  # whatever the roll-up, say what the uncertain read did not find
        summary.append(
            f"The read was uncertain and did not find: {', '.join(softened)}. Shown as "
            "review rather than missing; confirm on the image."
        )

    return VerificationResult(
        recommendation=recommendation,
        fields=fields,
        image_quality=extraction.image_quality,
        beverage_type=resolved,
        rules_part=rules.part,
        beverage_type_inferred=source != "filed",
        summary=summary,
    )
