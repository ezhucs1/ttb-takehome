"""Data models shared by the verification engine.

Two families of models live here:

* ``ApplicationData`` describes what the applicant typed into the COLA form.
* ``LabelExtraction`` describes what an extractor read off the label image.

``verify()`` compares the two and produces a ``VerificationResult``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


def _clamp01(value: float) -> float:
    """Models occasionally report 1.2 or -0.1; keep confidence in range instead of failing."""
    return max(0.0, min(1.0, float(value)))


class BeverageType(StrEnum):
    DISTILLED_SPIRITS = "distilled_spirits"
    WINE = "wine"
    MALT_BEVERAGE = "malt_beverage"


class ApplicationData(BaseModel):
    """Fields from the COLA application (TTB Form 5100.31) that must match the label."""

    beverage_type: BeverageType | None = Field(
        default=None,
        description="The commodity class filed. None means not stated: the engine infers it "
        "from the label and applies that class's rules.",
    )
    brand_name: str
    class_type: str
    alcohol_content: str = Field(
        default="",
        description='As entered on the form, e.g. "45% Alc./Vol.", "90 proof", or "13.5".',
    )
    net_contents: str = Field(default="", description='As entered on the form, e.g. "750 mL".')
    producer_name: str = ""
    producer_address: str = ""
    is_import: bool = False
    country_of_origin: str = ""


class ExtractedField(BaseModel):
    """One value read from the label, with the extractor's confidence in it."""

    value: str | None = Field(
        default=None,
        description="Text exactly as printed on the label, or null when not visible.",
    )
    confidence: float = Field(
        default=0.0,
        description="0 when the field is absent or unreadable, 1 when clearly legible.",
    )

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return _clamp01(value)


class HealthWarningExtraction(BaseModel):
    """The Government Health Warning Statement as it appears on the label."""

    present: bool = Field(description="True when any health warning statement is visible.")
    text: str | None = Field(
        default=None,
        description=(
            "Verbatim transcription of the entire statement, starting from the "
            "'GOVERNMENT WARNING' heading, preserving the original capitalization."
        ),
    )
    heading_all_caps: bool | None = Field(
        default=None,
        description="True when the words 'GOVERNMENT WARNING' are printed entirely in capitals.",
    )
    heading_bold: bool | None = Field(
        default=None,
        description="True when the 'GOVERNMENT WARNING' heading is visibly bolder than the body.",
    )
    confidence: float = Field(default=0.0)

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return _clamp01(value)


class ImageQuality(BaseModel):
    readable: bool = Field(description="False when the label text cannot be reliably read.")
    issues: list[str] = Field(
        default_factory=list,
        description="Short notes such as 'glare over lower third' or 'photographed at an angle'.",
    )


class LabelExtraction(BaseModel):
    """Everything an extractor reports about one label image."""

    brand_name: ExtractedField = Field(default_factory=ExtractedField)
    fanciful_name: ExtractedField = Field(
        default_factory=ExtractedField,
        description=(
            "A product name printed in addition to the brand ('Spartan Select' under "
            "'Harper's'; 'Chill Rasputin' under 'North Coast Brewing Co.'), or null."
        ),
    )
    class_type: ExtractedField = Field(default_factory=ExtractedField)
    alcohol_content: ExtractedField = Field(default_factory=ExtractedField)
    net_contents: ExtractedField = Field(default_factory=ExtractedField)
    producer_name: ExtractedField = Field(default_factory=ExtractedField)
    producer_address: ExtractedField = Field(default_factory=ExtractedField)
    country_of_origin: ExtractedField = Field(default_factory=ExtractedField)
    sulfite_declaration: ExtractedField = Field(
        default_factory=ExtractedField,
        description="The sulfite statement as printed, for example 'Contains Sulfites', or null.",
    )
    qualifying_phrase: ExtractedField = Field(
        default_factory=ExtractedField,
        description=(
            "The words that introduce the producer, as printed: 'Distilled and Bottled by', "
            "'Produced and Bottled by', 'Brewed by', 'Imported by', or null if none."
        ),
    )
    importer_statement: ExtractedField = Field(
        default_factory=ExtractedField,
        description="The 'Imported by ...' statement as printed, naming the U.S. importer, or null.",
    )
    age_statement: ExtractedField = Field(
        default_factory=ExtractedField,
        description="Any statement of age as printed, for example 'Aged 6 Years', or null.",
    )
    bottled_in_bond_claim: ExtractedField = Field(
        default_factory=ExtractedField,
        description="'Bottled in Bond' or 'Bonded' as printed if the label makes that claim, else null.",
    )
    blend_percentage: ExtractedField = Field(
        default_factory=ExtractedField,
        description="A percentage statement for a blend as printed, for example '51% Straight Bourbon Whiskey', or null.",
    )
    appellation: ExtractedField = Field(
        default_factory=ExtractedField,
        description=(
            "Wine: the appellation of origin printed as the wine's origin, for example "
            "'Napa Valley' or 'California'; not the city and state in the bottler's address."
        ),
    )
    vintage_year: ExtractedField = Field(
        default_factory=ExtractedField,
        description="Wine: the vintage year as printed, for example '2021', or null.",
    )
    estate_bottled_claim: ExtractedField = Field(
        default_factory=ExtractedField,
        description="Wine: 'Estate Bottled' as printed when the label makes that claim, else null.",
    )
    strength_claim: ExtractedField = Field(
        default_factory=ExtractedField,
        description=(
            "Malt beverage: any wording that emphasizes alcoholic strength as printed, such "
            "as 'Extra Strength', 'Strong', or 'High Test'; null if none."
        ),
    )
    product_category: ExtractedField = Field(
        default_factory=ExtractedField,
        description=(
            "Which TTB class the product is, judged from every cue on the label: exactly one of "
            "'distilled_spirits', 'wine', or 'malt_beverage', or null if it cannot be told."
        ),
    )
    health_warning: HealthWarningExtraction = Field(
        default_factory=lambda: HealthWarningExtraction(present=False)
    )
    image_quality: ImageQuality = Field(default_factory=lambda: ImageQuality(readable=True))


class Verdict(StrEnum):
    MATCH = "match"
    NEEDS_REVIEW = "needs_review"
    MISMATCH = "mismatch"
    NOT_APPLICABLE = "not_applicable"


class Recommendation(StrEnum):
    APPROVE = "approve"
    NEEDS_REVIEW = "needs_review"
    REQUEST_CORRECTION = "request_correction"


class WordDiff(BaseModel):
    """One token of a word-level diff, used to render the warning statement comparison."""

    op: str = Field(description="'equal', 'missing', or 'extra'")
    text: str = Field(description="Normalized token used for the comparison.")
    display: str = Field(default="", description="The word as printed, for rendering.")


class FieldResult(BaseModel):
    field: str
    label: str = Field(description="Human-readable field name for the UI.")
    verdict: Verdict
    application_value: str
    label_value: str | None
    confidence: float
    reason: str
    similarity: float | None = None
    notes: list[str] = Field(default_factory=list)
    diff: list[WordDiff] | None = None
    uncertain: bool = Field(
        default=False,
        description="The values agree but the read was low confidence, so the row still "
        "needs a human look; the verdict stays what the comparison found.",
    )
    citation: str | None = Field(default=None, description="The CFR section the row rests on.")
    requirement: str = Field(
        default="",
        description="required, optional, or conditional for this class; blank for a row "
        "that is not one of the class's fields (the type-of-product row).",
    )


class VerificationResult(BaseModel):
    recommendation: Recommendation
    fields: list[FieldResult]
    image_quality: ImageQuality
    beverage_type: BeverageType | None = Field(
        default=None, description="The class whose rules were applied."
    )
    rules_part: int | None = Field(default=None, description="27 CFR part applied: 4, 5, or 7.")
    beverage_type_inferred: bool = Field(
        default=False, description="True when the class came from the label, not the application."
    )
    summary: list[str] = Field(default_factory=list)
    extractor: str = ""
    extraction_ms: int = 0
    total_ms: int = 0
    reused_read: bool = Field(
        default=False,
        description="The comparison reused a read of the same images made earlier.",
    )
    extraction: LabelExtraction | None = Field(
        default=None, exclude=True, description="The read this result was computed from."
    )

    def field(self, name: str) -> FieldResult:
        for result in self.fields:
            if result.field == name:
                return result
        raise KeyError(name)
