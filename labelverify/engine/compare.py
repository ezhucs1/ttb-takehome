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
    normalize_text,
    parse_alcohol_content,
    parse_net_contents,
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
            reason = "Matches the application after normalization (capitalization, punctuation, spacing, or standard abbreviations such as CA for California)."
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


def _class_of(application: ApplicationData, extraction: LabelExtraction) -> ClassRules:
    """The rulebook to apply. An unresolved class is checked as distilled spirits, the
    strictest of the three, and the type-of-product row says so."""
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
    confidence = extraction.class_type.confidence if infer_beverage_category(designation) else extraction.product_category.confidence
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
            reason = f"The label shows '{extracted.value}' but the application value could not be read."
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
    if difference <= ABV_TOLERANCE:
        verdict = Verdict.MISMATCH if any("does not equal twice" in n for n in notes) else Verdict.MATCH
        reason = f"Both state {label_abv.abv:g}% alcohol by volume."
        if verdict is Verdict.MISMATCH:
            reason = "Percentage matches, but the proof printed on the label is inconsistent."
        elif notes:
            verdict = Verdict.NEEDS_REVIEW
    elif difference <= rules.abv_tolerance(label_abv.abv):
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

    rules = _class_of(application, extraction)
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
    return [
        f"{_fmt_ml(ml)} is not a listed standard of fill for {rules.name.lower()}. Confirm."
    ]


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
        if not importer and "import" not in f"{phrase} {extraction.producer_name.value or ''}".lower():
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
    if rules.requirement("age_statement") is Requirement.NOT_APPLICABLE or not _is_whisky(extraction):
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
            f"{claim} · {extraction.alcohol_content.value}" if extraction.alcohol_content.value else claim
        ),
        confidence=min(extraction.bottled_in_bond_claim.confidence, extraction.alcohol_content.confidence),
    )
    if abv is None:
        return FieldResult(
            verdict=Verdict.NEEDS_REVIEW,
            reason=f"The label claims '{claim}' but its alcohol content could not be read to confirm 100 proof.",
            **base,
        )
    if abs(abv.abv - 50.0) <= ABV_TOLERANCE:
        return FieldResult(
            verdict=Verdict.MATCH, reason=f"'{claim}' at {abv.abv:g}% alcohol by volume, as required.", **base
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
    if rules.requirement("blend_percentage") is Requirement.NOT_APPLICABLE or "blend" not in designation:
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


_CONDITIONAL_COMPARATORS = (compare_age_statement, compare_bottled_in_bond, compare_blend_percentage)


def verify(application: ApplicationData, extraction: LabelExtraction) -> VerificationResult:
    """Compare every required field under the class's rules and roll the verdicts up."""
    resolved, source = resolve_beverage_type(application, extraction)
    rules = rules_for(resolved or BeverageType.DISTILLED_SPIRITS)
    fields = [_apply_confidence_gate(fn(application, extraction)) for fn in _COMPARATORS]
    fields.append(_apply_confidence_gate(compare_qualifying_phrase(application, extraction)))
    if rules.requirement("sulfite_declaration") is not Requirement.NOT_APPLICABLE:
        fields.append(_apply_confidence_gate(compare_sulfite_declaration(application, extraction)))
    for conditional in _CONDITIONAL_COMPARATORS:
        row = conditional(application, extraction)
        if row is not None:
            fields.append(_apply_confidence_gate(row))
    fields.append(_apply_confidence_gate(check_health_warning(extraction.health_warning)))
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
        beverage_type=resolved,
        rules_part=rules.part,
        beverage_type_inferred=source != "filed",
        summary=summary,
    )
