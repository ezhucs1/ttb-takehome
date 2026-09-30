"""Gemini extractor tests against a fake HTTP transport (no network, no key)."""

from __future__ import annotations

import json

import pytest

from labelverify.engine.extractors import gemini as gemini_module
from labelverify.engine.extractors import get_extractor
from labelverify.engine.extractors.base import ExtractionError
from labelverify.engine.extractors.gemini import (
    GeminiExtractor,
    _HttpStatus,
    pick_model,
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


MODEL_LISTING = {
    "models": [
        {"name": "models/gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-pro", "supportedGenerationMethods": ["generateContent"]},
        {
            "name": "models/gemini-3.9-flash-preview",
            "supportedGenerationMethods": ["generateContent"],
        },
        {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
    ]
}


@pytest.fixture(autouse=True)
def _fresh_discovery_cache():
    gemini_module._discovered.clear()
    yield
    gemini_module._discovered.clear()


class TestModelDiscovery:
    def test_newest_stable_flash_wins(self):
        assert pick_model(MODEL_LISTING["models"]) == "gemini-3.8-flash"

    def test_falls_back_to_other_flash_then_anything(self):
        assert (
            pick_model(
                [
                    {
                        "name": "models/gemini-4.0-flash-preview",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/gemini-4.0-flash-lite",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                ]
            )
            == "gemini-4.0-flash-preview"
        )
        assert (
            pick_model(
                [{"name": "models/gemini-9-pro", "supportedGenerationMethods": ["generateContent"]}]
            )
            == "gemini-9-pro"
        )
        assert (
            pick_model([{"name": "models/x", "supportedGenerationMethods": ["embedContent"]}])
            is None
        )

    def test_auto_model_is_discovered_once_and_reused(self, extraction):
        calls = []

        def list_transport(url, api_key, timeout):
            calls.append(url)
            return MODEL_LISTING

        transport = FakeTransport(
            gemini_response(extraction.model_dump_json()),
            gemini_response(extraction.model_dump_json()),
        )
        extractor = GeminiExtractor(
            api_key="k", model="auto", transport=transport, list_transport=list_transport
        )
        extractor.extract(b"x", "image/png")
        extractor.extract(b"x", "image/png")
        assert len(calls) == 1 and calls[0].endswith("/models?pageSize=200")
        assert all(
            c["url"].endswith("/models/gemini-3.8-flash:generateContent") for c in transport.calls
        )

    def test_retired_model_switches_to_the_one_google_names(self, extraction):
        body = json.dumps(
            {
                "error": {
                    "message": "This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features."
                }
            }
        )
        transport = FakeTransport(
            _HttpStatus(404, body), gemini_response(extraction.model_dump_json())
        )
        extractor = GeminiExtractor(api_key="k", model="gemini-2.5-flash", transport=transport)
        assert extractor.extract(b"x", "image/png") == extraction
        assert transport.calls[1]["url"].endswith("/models/gemini-3.8-flash:generateContent")
        assert extractor.model == "gemini-3.8-flash"

    def test_unknown_model_without_a_hint_uses_discovery(self, extraction):
        transport = FakeTransport(
            _HttpStatus(404, json.dumps({"error": {"message": "not found"}})),
            gemini_response(extraction.model_dump_json()),
        )
        extractor = GeminiExtractor(
            api_key="k",
            model="gemini-typo",
            transport=transport,
            list_transport=lambda *a: MODEL_LISTING,
        )
        extractor.extract(b"x", "image/png")
        assert extractor.model == "gemini-3.8-flash"

    def test_model_not_found_with_nothing_better_is_reported(self):
        transport = FakeTransport(_HttpStatus(404, json.dumps({"error": {"message": "gone"}})))
        listing = {
            "models": [
                {"name": "models/gemini-typo", "supportedGenerationMethods": ["generateContent"]}
            ]
        }
        with pytest.raises(ExtractionError, match="model not found"):
            GeminiExtractor(
                api_key="k",
                model="gemini-typo",
                transport=transport,
                list_transport=lambda *a: listing,
            ).extract(b"x", "image/png")


class TestGeminiExtractor:
    def test_request_shape_and_parsing(self, extraction: LabelExtraction, monkeypatch):
        monkeypatch.setenv("LABELVERIFY_GEMINI_THINKING", "0")
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

    def test_single_image_has_no_numbering_and_no_thinking_config_by_default(self, extraction):
        transport = FakeTransport(gemini_response(extraction.model_dump_json()))
        GeminiExtractor(api_key="k", model="gemini-3.8-flash", transport=transport).extract(
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
            GeminiExtractor(api_key="bad", model="gemini-3.8-flash", transport=transport).extract(
                b"x", "image/png"
            )

    def test_google_reports_bad_keys_as_400(self):
        body = json.dumps({"error": {"message": "API key not valid. Please pass a valid API key."}})
        transport = FakeTransport(_HttpStatus(400, body))
        with pytest.raises(ExtractionError, match="key was rejected"):
            GeminiExtractor(api_key="bad", model="gemini-3.8-flash", transport=transport).extract(
                b"x", "image/png"
            )

    def test_rate_limit_retries_with_backoff_then_reports(self, monkeypatch, extraction):
        monkeypatch.setattr("labelverify.engine.extractors.gemini.time.sleep", lambda s: None)
        ok = gemini_response(extraction.model_dump_json())
        overloaded = _HttpStatus(503, json.dumps({"error": {"message": "high demand"}}))
        transport = FakeTransport(overloaded, overloaded, overloaded, ok)
        extractor = GeminiExtractor(api_key="k", model="gemini-3.8-flash", transport=transport)
        assert extractor.extract(b"x", "image/png") == extraction
        assert len(transport.calls) == 4

        transport = FakeTransport(overloaded, overloaded, overloaded, overloaded)
        with pytest.raises(ExtractionError, match="after 3 retries.*overloaded"):
            GeminiExtractor(api_key="k", model="gemini-3.8-flash", transport=transport).extract(
                b"x", "image/png"
            )

        transport = FakeTransport(_HttpStatus(429, "{}"), ok)
        assert (
            GeminiExtractor(api_key="k", model="gemini-3.8-flash", transport=transport).extract(
                b"x", "image/png"
            )
            == extraction
        )
        assert len(transport.calls) == 2

        transport = FakeTransport(_HttpStatus(429, "{}"), _HttpStatus(429, "{}"))
        with pytest.raises(ExtractionError, match="rate limit"):
            GeminiExtractor(api_key="k", model="gemini-3.8-flash", transport=transport).extract(
                b"x", "image/png"
            )

    def test_blocked_and_truncated_responses(self):
        blocked = {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
        with pytest.raises(ExtractionError, match="declined"):
            GeminiExtractor(
                api_key="k", model="gemini-3.8-flash", transport=FakeTransport(blocked)
            ).extract(b"x", "image/png")
        truncated = gemini_response('{"brand_name": {"val', finish="MAX_TOKENS")
        with pytest.raises(ExtractionError, match="incomplete"):
            GeminiExtractor(
                api_key="k", model="gemini-3.8-flash", transport=FakeTransport(truncated)
            ).extract(b"x", "image/png")

    def test_schema_mismatch_is_reported(self):
        transport = FakeTransport(gemini_response('{"brand_name": "not an object"}'))
        with pytest.raises(ExtractionError, match="did not match the schema"):
            GeminiExtractor(api_key="k", model="gemini-3.8-flash", transport=transport).extract(
                b"x", "image/png"
            )

    def test_out_of_range_confidence_is_clamped(self, extraction):
        payload = extraction.model_dump()
        payload["brand_name"]["confidence"] = 1.3
        transport = FakeTransport(gemini_response(json.dumps(payload)))
        result = GeminiExtractor(
            api_key="k", model="gemini-3.8-flash", transport=transport
        ).extract(b"x", "image/png")
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
