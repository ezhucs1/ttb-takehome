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
    normalize_address,
    normalize_country,
    normalize_text,
    parse_alcohol_content,
    parse_net_contents,
)
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

ABV_TOLERANCE = 0.05  # percentage points
VOLUME_TOLERANCE_ML = 1.0

# Common standards of fill (27 CFR 5.203 and 4.72). Advisory only: the list changes as TTB
# adds sizes, so a miss produces a note rather than a verdict.
SPIRITS_STANDARDS_ML = {50, 100, 200, 375, 700, 720, 750, 900, 1000, 1500, 1750, 1800}
WINE_STANDARDS_ML = {50, 100, 187, 200, 250, 355, 375, 500, 750, 1000, 1500, 3000}

_CLASS_SYNONYMS: dict[str, str] = {
    "whisky": "whiskey",
    "cabernet": "cabernet sauvignon",
    "ipa": "india pale ale",
}


def _apply_synonyms(text: str) -> str:
    words = [_CLASS_SYNONYMS.get(w, w) for w in text.split()]
    return " ".join(words)


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
) -> FieldResult:
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
            reason = "Matches the application (differences are only capitalization, punctuation, or spacing)."
    elif score >= review_at:
        verdict = Verdict.NEEDS_REVIEW
        reason = f"Similar but not identical to the application ({score:.0f}% similar). Confirm visually."
    else:
        verdict = Verdict.MISMATCH
        reason = f"Does not match the application ({score:.0f}% similar)."
    return FieldResult(verdict=verdict, reason=reason, similarity=round(score, 1), **base)


def compare_brand_name(application: ApplicationData, extraction: LabelExtraction) -> FieldResult:
    return _fuzzy_field(
        field="brand_name",
        label="Brand Name",
        application_value=application.brand_name,
        extracted=extraction.brand_name,
        review_at=BRAND_REVIEW,
    )


def compare_class_type(application: ApplicationData, extraction: LabelExtraction) -> FieldResult:
    return _fuzzy_field(
        field="class_type",
        label="Class / Type",
        application_value=application.class_type,
        extracted=extraction.class_type,
        review_at=CLASS_REVIEW,
        normalizer=lambda s: _apply_synonyms(normalize_text(s)),
    )


def compare_producer_name(application: ApplicationData, extraction: LabelExtraction) -> FieldResult:
    return _fuzzy_field(
        field="producer_name",
        label="Producer / Bottler Name",
        application_value=application.producer_name,
        extracted=extraction.producer_name,
        review_at=PRODUCER_REVIEW,
    )


def compare_producer_address(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult:
    return _fuzzy_field(
        field="producer_address",
        label="Producer / Bottler Address",
        application_value=application.producer_address,
        extracted=extraction.producer_address,
        review_at=ADDRESS_REVIEW,
        normalizer=normalize_address,
        required=False,
    )


def compare_alcohol_content(
    application: ApplicationData, extraction: LabelExtraction
) -> FieldResult:
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
    optional = application.beverage_type in (BeverageType.WINE, BeverageType.MALT_BEVERAGE)

    if app_abv is None and label_abv is None:
        if application.alcohol_content.strip() or (extracted.value or "").strip():
            return FieldResult(
                verdict=Verdict.NEEDS_REVIEW,
                reason="Could not read a percentage or proof value. Confirm visually.",
                **base,
            )
        if optional:
            return FieldResult(
                verdict=Verdict.NOT_APPLICABLE,
                reason="Not stated on the application or the label. Optional for some wines and malt beverages.",
                **base,
            )
        return FieldResult(
            verdict=Verdict.MISMATCH,
            reason="Alcohol content is required on distilled spirits labels and was not found.",
            **base,
        )
    if app_abv is None:
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW,
            reason=f"The label shows '{extracted.value}' but the application value could not be read.",
            **base,
        )
    if label_abv is None:
        if (extracted.value or "").strip():
            return FieldResult(
                verdict=Verdict.NEEDS_REVIEW,
                reason=f"The label shows '{extracted.value}', which could not be read as a percentage or proof. Confirm visually.",
                **base,
            )
        return FieldResult(
            verdict=Verdict.MISMATCH,
            reason=f"Application states {app_abv.abv:g}% but no alcohol content was found on the label.",
            **base,
        )

    notes: list[str] = []
    if label_abv.proof is not None and label_abv.source == "percent":
        if abs(label_abv.proof - 2 * label_abv.abv) > 0.1:
            notes.append(
                f"Label proof ({label_abv.proof:g}) does not equal twice the stated percentage."
            )
    if abs(app_abv.abv - label_abv.abv) <= ABV_TOLERANCE:
        verdict = Verdict.MISMATCH if notes else Verdict.MATCH
        reason = f"Both state {label_abv.abv:g}% alcohol by volume."
        if notes:
            reason = "Percentage matches, but the proof printed on the label is inconsistent."
    else:
        verdict = Verdict.MISMATCH
        reason = f"Application states {app_abv.abv:g}% but the label shows {label_abv.abv:g}%."
    return FieldResult(verdict=verdict, reason=reason, notes=notes, **base)


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

    if app_vol is None and label_vol is None:
        if application.net_contents.strip() or (extracted.value or "").strip():
            reason = "Could not read a volume. Confirm visually."
            return FieldResult(verdict=Verdict.NEEDS_REVIEW, reason=reason, **base)
        return FieldResult(
            verdict=Verdict.MISMATCH, reason="Net contents are required and were not found.", **base
        )
    if app_vol is None:
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW,
            reason=f"The label shows '{extracted.value}' but the application value could not be read.",
            **base,
        )
    if label_vol is None:
        if (extracted.value or "").strip():
            return FieldResult(
                verdict=Verdict.NEEDS_REVIEW,
                reason=f"The label shows '{extracted.value}', which could not be read as a volume. Confirm visually.",
                **base,
            )
        return FieldResult(
            verdict=Verdict.MISMATCH,
            reason=f"Application states {_fmt_ml(app_vol.milliliters)} but no net contents were found on the label.",
            **base,
        )

    notes = _standard_of_fill_notes(application.beverage_type, label_vol.milliliters)
    if abs(app_vol.milliliters - label_vol.milliliters) <= VOLUME_TOLERANCE_ML:
        return FieldResult(
            verdict=Verdict.MATCH,
            reason=f"Both state {_fmt_ml(label_vol.milliliters)}.",
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


def _fmt_ml(ml: float) -> str:
    return f"{ml:g} mL"


def _standard_of_fill_notes(beverage_type: BeverageType, ml: float) -> list[str]:
    standards = {
        BeverageType.DISTILLED_SPIRITS: SPIRITS_STANDARDS_ML,
        BeverageType.WINE: WINE_STANDARDS_ML,
    }.get(beverage_type)
    if standards is None:
        return []
    if any(abs(ml - s) <= VOLUME_TOLERANCE_ML for s in standards):
        return []
    return [f"{_fmt_ml(ml)} is not a common standard of fill for this beverage type. Confirm."]


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


def _apply_confidence_gate(result: FieldResult) -> FieldResult:
    """A match from a low-confidence read is never an unattended match."""
    if result.verdict is Verdict.MATCH and result.confidence < LOW_CONFIDENCE:
        result.verdict = Verdict.NEEDS_REVIEW
        result.notes.append(
            f"Low read confidence ({result.confidence:.0%}). Confirm the label text visually."
        )
    return result


_COMPARATORS = (
    compare_brand_name,
    compare_class_type,
    compare_alcohol_content,
    compare_net_contents,
    compare_producer_name,
    compare_producer_address,
    compare_country_of_origin,
)


def verify(application: ApplicationData, extraction: LabelExtraction) -> VerificationResult:
    """Compare every required field and roll the verdicts up into a recommendation."""
    fields = [_apply_confidence_gate(fn(application, extraction)) for fn in _COMPARATORS]
    fields.append(_apply_confidence_gate(check_health_warning(extraction.health_warning)))

    summary: list[str] = []
    if not extraction.image_quality.readable:
        summary.append("The image could not be read reliably. Ask for a clearer photo.")
    for issue in extraction.image_quality.issues:
        summary.append(f"Image note: {issue}")

    verdicts = {f.verdict for f in fields}
    if Verdict.MISMATCH in verdicts:
        recommendation = Recommendation.REQUEST_CORRECTION
        for f in fields:
            if f.verdict is Verdict.MISMATCH:
                summary.append(f"{f.label}: {f.reason}")
    elif Verdict.NEEDS_REVIEW in verdicts or not extraction.image_quality.readable:
        recommendation = Recommendation.NEEDS_REVIEW
        for f in fields:
            if f.verdict is Verdict.NEEDS_REVIEW:
                summary.append(f"{f.label}: {f.reason}")
    else:
        recommendation = Recommendation.APPROVE
        summary.append("All required fields match the application.")

    return VerificationResult(
        recommendation=recommendation,
        fields=fields,
        image_quality=extraction.image_quality,
        summary=summary,
    )
