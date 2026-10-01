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
from labelverify.engine.extractors.claude import (
    OUTPUT_INSTRUCTIONS,
    SYSTEM_PROMPT,
    ClaudeExtractor,
)
from labelverify.engine.extractors.tesseract import classify_text
from labelverify.engine.models import LabelExtraction
from labelverify.engine.warning import STATUTORY_TEXT


class FakeMessages:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []
        self.methods: list[str] = []

    def _answer(self, method: str, kwargs: dict):
        self.calls.append(kwargs)
        self.methods.append(method)
        if self.error:
            raise self.error
        return self.response

    def parse(self, **kwargs):
        return self._answer("parse", kwargs)

    def create(self, **kwargs):
        return self._answer("create", kwargs)


def fake_client(response=None, error=None):
    messages = FakeMessages(response, error)
    return SimpleNamespace(messages=messages), messages


def text_response(extraction: LabelExtraction, *, wrap: str = "{json}", stop="end_turn"):
    """What ``messages.create`` returns: the extraction as a JSON text block."""
    text = wrap.format(json=extraction.model_dump_json())
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=1200, output_tokens=640),
        _request_id="req_test",
    )


class TestClaudeExtractor:
    def test_each_extra_panel_gets_more_time(self, extraction: LabelExtraction):
        """A front-and-back set is one call with two images; it is given 1.5x the base
        timeout, and a timeout message says how long it waited and for how many images."""
        client, messages = fake_client(text_response(extraction))
        extractor = ClaudeExtractor(client, model="claude-sonnet-5-5", timeout=20)
        extractor.extract(b"a", "image/jpeg")
        extractor.extract_panels([(b"a", "image/jpeg"), (b"b", "image/jpeg")])
        extractor.extract_panels([(b"a", "image/jpeg")] * 3)
        assert [c["timeout"] for c in messages.calls] == [20, 30, 40]

        client, _ = fake_client(error=anthropic.APITimeoutError(request=None))
        with pytest.raises(ExtractionError, match="within 30 s for 2 images"):
            ClaudeExtractor(client, timeout=20).extract_panels(
                [(b"a", "image/jpeg"), (b"b", "image/jpeg")]
            )

    def test_request_shape_and_parsed_output(self, extraction: LabelExtraction):
        """By default the read is a plain ``messages.create``: the JSON shape is in the
        prompt and the reply is validated here, so no schema grammar is compiled server-side."""
        client, messages = fake_client(text_response(extraction))
        extractor = ClaudeExtractor(client, model="claude-opus-5-5", effort="low")

        result = extractor.extract(b"\xff\xd8fake", "image/jpeg")

        assert result == extraction
        assert messages.methods == ["create"]
        call = messages.calls[0]
        assert call["model"] == "claude-opus-5-5"
        assert call["system"].startswith(SYSTEM_PROMPT)
        assert OUTPUT_INSTRUCTIONS in call["system"]
        assert "output_format" not in call
        assert call["output_config"] == {"effort": "low"}
        assert extractor.last_usage == {"input_tokens": 1200, "output_tokens": 640}
        assert extractor.last_request_id == "req_test"
        content = call["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["media_type"] == "image/jpeg"
        assert content[0]["source"]["data"] == "/9hmYWtl"  # base64 of the bytes above
        assert content[1]["type"] == "text"

    def test_structured_output_mode_is_opt_in(self, extraction: LabelExtraction, monkeypatch):
        """``LABELVERIFY_STRUCTURED_OUTPUT=true`` restores the schema-constrained call."""
        response = SimpleNamespace(stop_reason="end_turn", parsed_output=extraction, usage=None)
        client, messages = fake_client(response)
        monkeypatch.setenv("LABELVERIFY_STRUCTURED_OUTPUT", "true")
        assert ClaudeExtractor(client).extract(b"x", "image/jpeg") == extraction
        assert messages.methods == ["parse"]
        assert messages.calls[0]["output_format"] is LabelExtraction
        assert messages.calls[0]["system"] == SYSTEM_PROMPT

    def test_reply_wrapped_in_fences_or_prose_still_parses(self, extraction: LabelExtraction):
        for wrap in ("```json\n{json}\n```", "Here is the extraction:\n{json}\nDone."):
            client, _ = fake_client(text_response(extraction, wrap=wrap))
            assert ClaudeExtractor(client).extract(b"x", "image/jpeg") == extraction

    def test_reply_that_is_not_label_json_is_an_extraction_error(self):
        for text in ("I cannot read this.", '{"brand_name": "not an object"}'):
            response = SimpleNamespace(
                stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)]
            )
            client, _ = fake_client(response)
            with pytest.raises(ExtractionError, match="not (a JSON object|valid label JSON)"):
                ClaudeExtractor(client).extract(b"x", "image/jpeg")

    def test_multiple_panels_are_numbered_in_one_request(self, extraction: LabelExtraction):
        client, messages = fake_client(text_response(extraction))
        ClaudeExtractor(client).extract_panels([(b"front", "image/jpeg"), (b"back", "image/png")])
        content = messages.calls[0]["messages"][0]["content"]
        assert [c["type"] for c in content] == ["text", "image", "text", "image", "text"]
        assert content[0]["text"] == "Label image 1 of 2:"
        assert content[3]["source"]["media_type"] == "image/png"
        assert "these 2 label images" in content[-1]["text"]

    def test_confidence_outside_range_is_clamped_not_rejected(self):
        from labelverify.engine.models import ExtractedField

        assert ExtractedField(value="x", confidence=1.4).confidence == 1.0
        assert ExtractedField(value="x", confidence=-0.2).confidence == 0.0

    def test_haiku_is_called_without_effort(self, extraction: LabelExtraction):
        client, messages = fake_client(text_response(extraction))
        ClaudeExtractor(client, model="claude-haiku-4-5").extract(b"x", "image/jpeg")
        assert "output_config" not in messages.calls[0]
        assert messages.calls[0]["model"] == "claude-haiku-4-5"

    def test_refusal_raises_extraction_error(self):
        client, _ = fake_client(SimpleNamespace(stop_reason="refusal", content=[]))
        with pytest.raises(ExtractionError, match="declined"):
            ClaudeExtractor(client).extract(b"x", "image/png")

    def test_truncated_output_raises_extraction_error(self):
        client, _ = fake_client(SimpleNamespace(stop_reason="max_tokens", content=[]))
        with pytest.raises(ExtractionError, match="incomplete"):
            ClaudeExtractor(client).extract(b"x", "image/png")

    def test_timeout_is_wrapped(self):
        client, _ = fake_client(error=anthropic.APITimeoutError(request=None))
        with pytest.raises(ExtractionError, match="did not respond within 20 s"):
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
    def test_default_is_claude_when_a_key_is_configured(self, monkeypatch):
        monkeypatch.delenv("LABELVERIFY_EXTRACTOR", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert get_extractor().name == "claude"

    def test_default_is_demo_without_a_key(self, monkeypatch):
        monkeypatch.delenv("LABELVERIFY_EXTRACTOR", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert get_extractor().name == "demo"

    def test_environment_selects_tesseract(self, monkeypatch):
        monkeypatch.setenv("LABELVERIFY_EXTRACTOR", "tesseract")
        assert get_extractor().name == "tesseract"

    def test_demo_extractor_reads_any_known_panel(self):
        from labelverify.engine.extractors import DemoExtractor
        from labelverify.engine.extractors.demo import SAMPLES_DIR, load_manifest

        known = (SAMPLES_DIR / load_manifest()[0]["file"]).read_bytes()
        result = DemoExtractor().extract_panels(
            [(b"unknown back label", "image/png"), (known, "image/jpeg")]
        )
        assert result.brand_name.value

    def test_unknown_name_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown extractor"):
            get_extractor("magic")

    def test_fixture_extractor_returns_a_copy(self, extraction):
        fixture = FixtureExtractor(extraction)
        first = fixture.extract(b"", "image/png")
        first.brand_name.value = "changed"
        assert fixture.extract(b"", "image/png").brand_name.value == "OLD TOM DISTILLERY"


class TestDailyBudget:
    def test_budget_refuses_after_the_limit_and_resets_next_day(self, extraction):
        from datetime import UTC, datetime

        from labelverify.engine.extractors.budget import BudgetedExtractor, BudgetExhausted

        inner = FixtureExtractor(extraction)
        inner.name = "claude"  # pretend it costs money
        now = [datetime(2026, 10, 1, 23, 59, tzinfo=UTC)]
        budget = BudgetedExtractor(inner, 2, clock=lambda: now[0])
        assert budget.name == "claude" and budget.remaining == 2
        budget.extract(b"x", "image/png")
        budget.extract_panels([(b"x", "image/png")])
        assert budget.remaining == 0 and len(inner.calls) == 2
        with pytest.raises(BudgetExhausted, match="reading budget of 2"):
            budget.extract(b"x", "image/png")
        assert len(inner.calls) == 2  # the refused call never reached the model
        now[0] = datetime(2026, 10, 2, 0, 1, tzinfo=UTC)
        budget.extract(b"x", "image/png")
        assert budget.used_today == 1

    def test_with_budget_wraps_only_paid_extractors_when_configured(self, extraction, monkeypatch):
        from labelverify.engine.extractors.budget import BudgetedExtractor, with_budget

        monkeypatch.delenv("LABELVERIFY_DAILY_READ_LIMIT", raising=False)
        inner = FixtureExtractor(extraction)
        assert with_budget(inner) is inner  # nothing configured
        monkeypatch.setenv("LABELVERIFY_DAILY_READ_LIMIT", "5")
        assert with_budget(inner) is inner  # fixtures are free
        inner.name = "gemini"
        wrapped = with_budget(inner)
        assert isinstance(wrapped, BudgetedExtractor) and wrapped.limit == 5


def test_prompted_json_shape_names_every_extraction_field():
    """The reply shape in the prompt and the pydantic model cannot drift apart: a field
    added to one without the other would be silently null on every read."""
    from labelverify.engine.extractors.claude import _FIELD_KEYS

    per_field = set(LabelExtraction.model_fields) - {"health_warning", "image_quality"}
    assert set(_FIELD_KEYS) == per_field
    assert len(_FIELD_KEYS) == len(set(_FIELD_KEYS))
