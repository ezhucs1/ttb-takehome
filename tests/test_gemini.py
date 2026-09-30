"""Gemini extractor tests against a fake HTTP transport (no network, no key)."""

from __future__ import annotations

import json

import pytest

from labelverify.engine.extractors import get_extractor
from labelverify.engine.extractors.base import ExtractionError
from labelverify.engine.extractors.gemini import (
    GeminiExtractor,
    _HttpStatus,
    to_gemini_schema,
)
from labelverify.engine.models import LabelExtraction


def gemini_response(text: str, finish: str = "STOP") -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, api_key, payload, timeout):
        self.calls.append({"url": url, "api_key": api_key, "payload": payload, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestSchemaConversion:
    def test_refs_are_inlined_and_nullables_marked(self):
        schema = to_gemini_schema(LabelExtraction.model_json_schema())
        assert "$defs" not in json.dumps(schema)
        brand = schema["properties"]["brand_name"]
        assert brand["type"] == "object"
        assert brand["properties"]["value"] == {
            "type": "string",
            "nullable": True,
            "description": "Text exactly as printed on the label, or null when not visible.",
        }
        assert "title" not in json.dumps(schema) and "default" not in json.dumps(schema)

    def test_every_property_is_required_so_nothing_is_omitted(self):
        schema = to_gemini_schema(LabelExtraction.model_json_schema())
        assert set(schema["required"]) == set(schema["properties"])
        warning = schema["properties"]["health_warning"]
        assert warning["properties"]["heading_bold"] == {
            "type": "boolean",
            "nullable": True,
            "description": "True when the 'GOVERNMENT WARNING' heading is visibly bolder than the body.",
        }
        assert schema["properties"]["image_quality"]["properties"]["issues"]["items"] == {
            "type": "string"
        }


class TestGeminiExtractor:
    def test_request_shape_and_parsing(self, extraction: LabelExtraction):
        transport = FakeTransport(gemini_response(extraction.model_dump_json()))
        extractor = GeminiExtractor(
            api_key="test-key", model="gemini-2.5-flash", transport=transport
        )

        result = extractor.extract_panels([(b"front", "image/jpeg"), (b"back", "image/png")])

        assert result == extraction
        call = transport.calls[0]
        assert call["url"].endswith("/models/gemini-2.5-flash:generateContent")
        assert call["api_key"] == "test-key"
        parts = call["payload"]["contents"][0]["parts"]
        assert parts[0] == {"text": "Label image 1 of 2:"}
        assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
        assert parts[1]["inline_data"]["data"] == "ZnJvbnQ="
        assert parts[3]["inline_data"]["mime_type"] == "image/png"
        assert "these 2 label images" in parts[-1]["text"]
        config = call["payload"]["generationConfig"]
        assert config["response_mime_type"] == "application/json"
        assert config["response_schema"]["type"] == "object"
        assert config["thinking_config"] == {"thinking_budget": 0}
        assert "GOVERNMENT WARNING" in call["payload"]["system_instruction"]["parts"][0]["text"]

    def test_single_image_has_no_numbering_and_older_models_skip_thinking_config(self, extraction):
        transport = FakeTransport(gemini_response(extraction.model_dump_json()))
        GeminiExtractor(api_key="k", model="gemini-2.0-flash", transport=transport).extract(
            b"x", "image/png"
        )
        payload = transport.calls[0]["payload"]
        assert [list(p)[0] for p in payload["contents"][0]["parts"]] == ["inline_data", "text"]
        assert "thinking_config" not in payload["generationConfig"]

    def test_missing_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ExtractionError, match="GEMINI_API_KEY"):
            GeminiExtractor().extract(b"x", "image/png")

    def test_rejected_key_is_explained(self):
        transport = FakeTransport(
            _HttpStatus(403, json.dumps({"error": {"message": "API key not valid"}}))
        )
        with pytest.raises(ExtractionError, match="key was rejected.*API key not valid"):
            GeminiExtractor(api_key="bad", transport=transport).extract(b"x", "image/png")

    def test_google_reports_bad_keys_as_400(self):
        body = json.dumps({"error": {"message": "API key not valid. Please pass a valid API key."}})
        transport = FakeTransport(_HttpStatus(400, body))
        with pytest.raises(ExtractionError, match="key was rejected"):
            GeminiExtractor(api_key="bad", transport=transport).extract(b"x", "image/png")

    def test_rate_limit_retries_once_then_reports(self, monkeypatch, extraction):
        monkeypatch.setattr("labelverify.engine.extractors.gemini.time.sleep", lambda s: None)
        ok = gemini_response(extraction.model_dump_json())
        transport = FakeTransport(_HttpStatus(429, "{}"), ok)
        assert (
            GeminiExtractor(api_key="k", transport=transport).extract(b"x", "image/png")
            == extraction
        )
        assert len(transport.calls) == 2

        transport = FakeTransport(_HttpStatus(429, "{}"), _HttpStatus(429, "{}"))
        with pytest.raises(ExtractionError, match="rate limit"):
            GeminiExtractor(api_key="k", transport=transport).extract(b"x", "image/png")

    def test_blocked_and_truncated_responses(self):
        blocked = {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
        with pytest.raises(ExtractionError, match="declined"):
            GeminiExtractor(api_key="k", transport=FakeTransport(blocked)).extract(
                b"x", "image/png"
            )
        truncated = gemini_response('{"brand_name": {"val', finish="MAX_TOKENS")
        with pytest.raises(ExtractionError, match="incomplete"):
            GeminiExtractor(api_key="k", transport=FakeTransport(truncated)).extract(
                b"x", "image/png"
            )

    def test_schema_mismatch_is_reported(self):
        transport = FakeTransport(gemini_response('{"brand_name": "not an object"}'))
        with pytest.raises(ExtractionError, match="did not match the schema"):
            GeminiExtractor(api_key="k", transport=transport).extract(b"x", "image/png")

    def test_out_of_range_confidence_is_clamped(self, extraction):
        payload = extraction.model_dump()
        payload["brand_name"]["confidence"] = 1.3
        transport = FakeTransport(gemini_response(json.dumps(payload)))
        result = GeminiExtractor(api_key="k", transport=transport).extract(b"x", "image/png")
        assert result.brand_name.confidence == 1.0


class TestRegistry:
    def test_gemini_is_default_when_only_its_key_is_set(self, monkeypatch):
        monkeypatch.delenv("LABELVERIFY_EXTRACTOR", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "g")
        assert get_extractor().name == "gemini"

    def test_claude_wins_when_both_keys_are_set(self, monkeypatch):
        monkeypatch.delenv("LABELVERIFY_EXTRACTOR", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
        monkeypatch.setenv("GEMINI_API_KEY", "g")
        assert get_extractor().name == "claude"

    def test_explicit_choice_wins(self, monkeypatch):
        monkeypatch.setenv("LABELVERIFY_EXTRACTOR", "gemini")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
        assert get_extractor().name == "gemini"
