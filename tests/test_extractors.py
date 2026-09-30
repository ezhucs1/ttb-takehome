"""Extractor tests.

The Claude extractor is tested against a fake client so the suite runs offline; the
Tesseract extractor's classification heuristics are tested on raw OCR text so the
binary is not required.
"""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import pytest

from labelverify.engine.extractors import FixtureExtractor, get_extractor
from labelverify.engine.extractors.base import ExtractionError
from labelverify.engine.extractors.claude import SYSTEM_PROMPT, ClaudeExtractor
from labelverify.engine.extractors.tesseract import classify_text
from labelverify.engine.models import LabelExtraction
from labelverify.engine.warning import STATUTORY_TEXT


class FakeMessages:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_client(response=None, error=None):
    messages = FakeMessages(response, error)
    return SimpleNamespace(messages=messages), messages


class TestClaudeExtractor:
    def test_request_shape_and_parsed_output(self, extraction: LabelExtraction):
        response = SimpleNamespace(stop_reason="end_turn", parsed_output=extraction)
        client, messages = fake_client(response)
        extractor = ClaudeExtractor(client, model="claude-opus-5-5", effort="low")

        result = extractor.extract(b"\xff\xd8fake", "image/jpeg")

        assert result == extraction
        call = messages.calls[0]
        assert call["model"] == "claude-opus-5-5"
        assert call["system"] == SYSTEM_PROMPT
        assert call["output_format"] is LabelExtraction
        assert call["output_config"] == {"effort": "low"}
        content = call["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["media_type"] == "image/jpeg"
        assert content[0]["source"]["data"] == "/9hmYWtl"  # base64 of the bytes above
        assert content[1]["type"] == "text"

    def test_refusal_raises_extraction_error(self):
        client, _ = fake_client(SimpleNamespace(stop_reason="refusal", parsed_output=None))
        with pytest.raises(ExtractionError, match="declined"):
            ClaudeExtractor(client).extract(b"x", "image/png")

    def test_truncated_output_raises_extraction_error(self):
        client, _ = fake_client(SimpleNamespace(stop_reason="max_tokens", parsed_output=None))
        with pytest.raises(ExtractionError, match="incomplete"):
            ClaudeExtractor(client).extract(b"x", "image/png")

    def test_timeout_is_wrapped(self):
        client, _ = fake_client(error=anthropic.APITimeoutError(request=None))
        with pytest.raises(ExtractionError, match="time limit"):
            ClaudeExtractor(client).extract(b"x", "image/png")

    def test_missing_api_key_is_a_clear_error(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ExtractionError, match="ANTHROPIC_API_KEY"):
            ClaudeExtractor().extract(b"x", "image/png")

    def test_model_and_timeout_come_from_environment(self, monkeypatch):
        monkeypatch.setenv("LABELVERIFY_MODEL", "claude-haiku-4-5")
        monkeypatch.setenv("LABELVERIFY_EXTRACT_TIMEOUT", "7.5")
        extractor = ClaudeExtractor()
        assert extractor.model == "claude-haiku-4-5"
        assert extractor.timeout == 7.5


OCR_TEXT = f"""OLD TOM DISTILLERY

Kentucky Straight Bourbon Whiskey

45% Alc./Vol. (90 Proof)
750 mL

Distilled and Bottled by Old Tom Distillery
Bardstown, KY 40004

{STATUTORY_TEXT}
"""


class TestTesseractClassification:
    def test_fields_are_assigned_from_ocr_text(self):
        result = classify_text(OCR_TEXT)
        assert result.brand_name.value == "OLD TOM DISTILLERY"
        assert result.class_type.value == "Kentucky Straight Bourbon Whiskey"
        assert result.alcohol_content.value == "45% Alc./Vol. (90 Proof)"
        assert result.net_contents.value == "750 mL"
        assert result.producer_name.value == "Old Tom Distillery"
        assert result.producer_address.value == "Bardstown, KY 40004"
        assert result.country_of_origin.value is None
        assert result.health_warning.present is True
        assert result.health_warning.text == STATUTORY_TEXT
        assert result.health_warning.heading_all_caps is True
        assert result.health_warning.heading_bold is None
        assert result.image_quality.readable is True

    def test_confidences_are_low_so_results_get_human_review(self):
        result = classify_text(OCR_TEXT)
        assert all(
            f.confidence < 0.6
            for f in (result.brand_name, result.class_type, result.alcohol_content)
        )

    def test_country_of_origin_is_found(self):
        result = classify_text("GLEN MORAY\nSingle Malt Scotch Whisky\nProduct of Scotland\n70 cl")
        assert result.country_of_origin.value == "Scotland"
        assert result.net_contents.value == "70 cl"

    def test_title_case_warning_is_detected_as_not_caps(self):
        text = STATUTORY_TEXT.replace("GOVERNMENT WARNING", "Government Warning")
        result = classify_text(f"BRAND\n{text}")
        assert result.health_warning.present is True
        assert result.health_warning.heading_all_caps is False

    def test_empty_text_is_marked_unreadable(self):
        result = classify_text("   \n  ")
        assert result.image_quality.readable is False
        assert result.health_warning.present is False


class TestRegistry:
    def test_default_is_claude(self, monkeypatch):
        monkeypatch.delenv("LABELVERIFY_EXTRACTOR", raising=False)
        assert get_extractor().name == "claude"

    def test_environment_selects_tesseract(self, monkeypatch):
        monkeypatch.setenv("LABELVERIFY_EXTRACTOR", "tesseract")
        assert get_extractor().name == "tesseract"

    def test_unknown_name_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown extractor"):
            get_extractor("magic")

    def test_fixture_extractor_returns_a_copy(self, extraction):
        fixture = FixtureExtractor(extraction)
        first = fixture.extract(b"", "image/png")
        first.brand_name.value = "changed"
        assert fixture.extract(b"", "image/png").brand_name.value == "OLD TOM DISTILLERY"
