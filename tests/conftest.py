from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from labelverify.engine.models import (
    ApplicationData,
    BeverageType,
    ExtractedField,
    HealthWarningExtraction,
    ImageQuality,
    LabelExtraction,
)
from labelverify.engine.warning import STATUTORY_TEXT


def make_field(value: str | None, confidence: float = 0.95) -> ExtractedField:
    return ExtractedField(value=value, confidence=confidence if value else 0.0)


@pytest.fixture
def application() -> ApplicationData:
    """The sample distilled spirits application from the assignment brief."""
    return ApplicationData(
        beverage_type=BeverageType.DISTILLED_SPIRITS,
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        producer_name="Old Tom Distillery",
        producer_address="123 Barrel Lane, Bardstown, Kentucky 40004",
        is_import=False,
        country_of_origin="",
    )


@pytest.fixture
def extraction() -> LabelExtraction:
    """A clean read of a label that matches ``application`` exactly."""
    return LabelExtraction(
        brand_name=make_field("OLD TOM DISTILLERY"),
        class_type=make_field("Kentucky Straight Bourbon Whiskey"),
        alcohol_content=make_field("45% Alc./Vol. (90 Proof)"),
        net_contents=make_field("750 mL"),
        producer_name=make_field("Old Tom Distillery"),
        producer_address=make_field("123 Barrel Ln., Bardstown, KY 40004"),
        country_of_origin=make_field(None),
        health_warning=HealthWarningExtraction(
            present=True,
            text=STATUTORY_TEXT,
            heading_all_caps=True,
            heading_bold=True,
            confidence=0.97,
        ),
        image_quality=ImageQuality(readable=True, issues=[]),
    )


@pytest.fixture
def label_png() -> bytes:
    """A small synthetic label image with real text, for preprocessing and OCR tests."""
    image = Image.new("RGB", (800, 1200), "white")
    draw = ImageDraw.Draw(image)
    lines = [
        "OLD TOM DISTILLERY",
        "Kentucky Straight Bourbon Whiskey",
        "45% Alc./Vol. (90 Proof)",
        "750 mL",
        "Distilled and Bottled by Old Tom Distillery",
        "Bardstown, KY 40004",
    ]
    y = 100
    for line in lines:
        draw.text((60, y), line, fill="black")
        y += 60
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
