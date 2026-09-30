"""Verification engine: models, normalizers, comparison rules, and extractors."""

from .compare import verify
from .models import (
    ApplicationData,
    BeverageType,
    ExtractedField,
    FieldResult,
    HealthWarningExtraction,
    ImageQuality,
    LabelExtraction,
    Recommendation,
    Verdict,
    VerificationResult,
)
from .verify import run_verification

__all__ = [
    "ApplicationData",
    "BeverageType",
    "ExtractedField",
    "FieldResult",
    "HealthWarningExtraction",
    "ImageQuality",
    "LabelExtraction",
    "Recommendation",
    "Verdict",
    "VerificationResult",
    "run_verification",
    "verify",
]
