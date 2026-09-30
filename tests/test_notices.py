"""Correction notice drafting: template content, provider selection, fallbacks."""

from __future__ import annotations

import pytest

from labelverify.engine import notices
from labelverify.engine.compare import verify
from labelverify.engine.models import ExtractedField, HealthWarningExtraction


@pytest.fixture
def failing_result(application, extraction):
    extraction.alcohol_content = ExtractedField(value="40% Alc./Vol. (80 Proof)", confidence=0.95)
    extraction.health_warning = HealthWarningExtraction(
        present=True,
        text="Government Warning: (1) According to the Surgeon General, women should not drink "
        "alcoholic beverages during pregnancy because of the risk of birth defects. (2) "
        "Consumption of alcoholic beverages impairs your ability to drive a car or operate "
        "machinery, and may cause health problems.",
        heading_all_caps=False,
        heading_bold=True,
        confidence=0.9,
    )
    return verify(application, extraction)


def test_template_lists_every_finding_with_values_and_fixes(application, failing_result):
    body = notices.template_notice(
        application, failing_result, serial="COLA-2026-TEST01", applicant_org="Old Tom Distillery"
    )
    assert "COLA-2026-TEST01" in body and "Dear Old Tom Distillery" in body
    assert "1. Alcohol Content" in body and "2. Government Health Warning" in body
    assert "Application states: 45% Alc./Vol. (90 Proof)" in body
    assert "Label shows: 40% Alc./Vol. (80 Proof)" in body
    assert "capital letters" in body and "27 CFR 16.21" in body
    assert "resubmit" in body.lower()


class TestProviderSelection:
    def test_explicit_setting_wins(self, monkeypatch):
        monkeypatch.setenv("LABELVERIFY_NOTICE_PROVIDER", "template")
        monkeypatch.setenv("GEMINI_API_KEY", "g")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
        assert notices.notice_provider() == "template"

    def test_gemini_preferred_when_its_key_exists(self, monkeypatch):
        monkeypatch.delenv("LABELVERIFY_NOTICE_PROVIDER", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "g")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
        assert notices.notice_provider() == "gemini"

    def test_claude_when_only_its_key_exists(self, monkeypatch):
        monkeypatch.delenv("LABELVERIFY_NOTICE_PROVIDER", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
        assert notices.notice_provider() == "claude"

    def test_template_without_keys(self, monkeypatch):
        monkeypatch.delenv("LABELVERIFY_NOTICE_PROVIDER", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert notices.notice_provider() == "template"


class TestDraft:
    def test_gemini_rewrite_is_used_and_labelled(self, monkeypatch, application, failing_result):
        monkeypatch.setenv("LABELVERIFY_NOTICE_PROVIDER", "gemini")
        seen = {}

        def fake_generate_text(prompt, *, system, **kw):
            seen["prompt"] = prompt
            seen["system"] = system
            return "Dear Old Tom Distillery, here is a friendlier version."

        monkeypatch.setattr(
            "labelverify.engine.extractors.gemini.generate_text", fake_generate_text
        )
        draft = notices.draft_notice(
            application, failing_result, serial="S", applicant_org="Old Tom Distillery"
        )
        assert draft.source == "gemini"
        assert draft.body.startswith("Dear Old Tom Distillery, here is a friendlier")
        assert "Label shows: 40% Alc./Vol. (80 Proof)" in seen["prompt"]  # findings go in
        assert "Keep every factual finding" in seen["system"]

    def test_provider_failure_falls_back_to_template(
        self, monkeypatch, application, failing_result
    ):
        monkeypatch.setenv("LABELVERIFY_NOTICE_PROVIDER", "gemini")

        def boom(prompt, *, system, **kw):
            raise RuntimeError("503 high demand")

        monkeypatch.setattr("labelverify.engine.extractors.gemini.generate_text", boom)
        draft = notices.draft_notice(application, failing_result, serial="S", applicant_org="Org")
        assert draft.source == "template" and "1. Alcohol Content" in draft.body

    def test_use_ai_false_skips_the_model(self, monkeypatch, application, failing_result):
        monkeypatch.setenv("LABELVERIFY_NOTICE_PROVIDER", "gemini")
        draft = notices.draft_notice(
            application, failing_result, serial="S", applicant_org="Org", use_ai=False
        )
        assert draft.source == "template"
