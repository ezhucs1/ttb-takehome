"""Data models shared by the verification engine.

Two families of models live here:

* ``ApplicationData`` describes what the applicant typed into the COLA form.
* ``LabelExtraction`` describes what an extractor read off the label image.

``verify()`` compares the two and produces a ``VerificationResult``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class BeverageType(StrEnum):
    DISTILLED_SPIRITS = "distilled_spirits"
    WINE = "wine"
    MALT_BEVERAGE = "malt_beverage"


class ApplicationData(BaseModel):
    """Fields from the COLA application (TTB Form 5100.31) that must match the label."""

    beverage_type: BeverageType
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
        ge=0.0,
        le=1.0,
        description="0 when the field is absent or unreadable, 1 when clearly legible.",
    )


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
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ImageQuality(BaseModel):
    readable: bool = Field(description="False when the label text cannot be reliably read.")
    issues: list[str] = Field(
        default_factory=list,
        description="Short notes such as 'glare over lower third' or 'photographed at an angle'.",
    )


class LabelExtraction(BaseModel):
    """Everything an extractor reports about one label image."""

    brand_name: ExtractedField = Field(default_factory=ExtractedField)
    class_type: ExtractedField = Field(default_factory=ExtractedField)
    alcohol_content: ExtractedField = Field(default_factory=ExtractedField)
    net_contents: ExtractedField = Field(default_factory=ExtractedField)
    producer_name: ExtractedField = Field(default_factory=ExtractedField)
    producer_address: ExtractedField = Field(default_factory=ExtractedField)
    country_of_origin: ExtractedField = Field(default_factory=ExtractedField)
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


class VerificationResult(BaseModel):
    recommendation: Recommendation
    fields: list[FieldResult]
    image_quality: ImageQuality
    summary: list[str] = Field(default_factory=list)
    extractor: str = ""
    extraction_ms: int = 0
    total_ms: int = 0

    def field(self, name: str) -> FieldResult:
        for result in self.fields:
            if result.field == name:
                return result
        raise KeyError(name)
