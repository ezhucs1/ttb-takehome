"""Correction notices sent from a specialist to an applicant.

A notice lists what failed, what the label shows versus what the application says,
and what the applicant should do. A deterministic template always works; when an API
key is configured the model rewrites the same facts into a friendlier letter. The
specialist edits either version before sending, so nothing goes out unreviewed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .models import ApplicationData, FieldResult, Verdict, VerificationResult

NOTICE_MODEL_ENV = "LABELVERIFY_NOTICE_MODEL"
DEFAULT_NOTICE_MODEL = "claude-opus-5-5"


@dataclass(frozen=True)
class NoticeDraft:
    body: str
    source: str  # "ai" or "template"


def flagged_fields(result: VerificationResult) -> list[FieldResult]:
    return [f for f in result.fields if f.verdict in (Verdict.MISMATCH, Verdict.NEEDS_REVIEW)]


def _fix_instruction(f: FieldResult) -> str:
    if f.field == "health_warning":
        return (
            "Print the Government Health Warning Statement exactly as required by 27 CFR 16.21, "
            "with 'GOVERNMENT WARNING' in capital letters and bold type."
        )
    if f.label_value is None:
        return "Add this information to the label, or correct the application if it was entered in error."
    return (
        "Make the label and the application agree. Either correct the label artwork or update the "
        "application to match what is printed."
    )


def template_notice(
    application: ApplicationData, result: VerificationResult, *, serial: str, applicant_org: str
) -> str:
    lines = [
        f"Re: COLA application {serial} for {application.brand_name}",
        "",
        f"Dear {applicant_org or 'Applicant'},",
        "",
        "We reviewed the label submitted with this application and found the following items "
        "that must be corrected before the label can be approved:",
        "",
    ]
    for n, f in enumerate(flagged_fields(result), start=1):
        lines.append(f"{n}. {f.label}")
        if f.field != "health_warning":
            lines.append(f"   Application states: {f.application_value or '(blank)'}")
            lines.append(f"   Label shows: {f.label_value or '(not found)'}")
        lines.append(f"   Finding: {f.reason}")
        lines.append(f"   Action: {_fix_instruction(f)}")
        lines.append("")
    lines += [
        "Please correct the items above and resubmit the revised label through the portal. "
        "Your application keeps its number and place in the queue. Reply on any item if you "
        "believe a finding is in error.",
        "",
        "Alcohol and Tobacco Tax and Trade Bureau",
        "Advertising, Labeling and Formulation Division",
    ]
    return "\n".join(lines)


def draft_notice(
    application: ApplicationData,
    result: VerificationResult,
    *,
    serial: str,
    applicant_org: str,
    use_ai: bool | None = None,
) -> NoticeDraft:
    """Template first; model rewrite when a key is configured and the rewrite succeeds."""
    template = template_notice(application, result, serial=serial, applicant_org=applicant_org)
    if use_ai is None:
        use_ai = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not use_ai:
        return NoticeDraft(body=template, source="template")
    try:
        body = _ai_rewrite(template)
    except Exception:  # any API failure falls back to the template; the specialist still edits
        return NoticeDraft(body=template, source="template")
    return NoticeDraft(body=body, source="ai")


def _ai_rewrite(template: str) -> str:
    import anthropic

    client = anthropic.Anthropic(max_retries=1, timeout=20.0)
    response = client.messages.create(
        model=os.environ.get(NOTICE_MODEL_ENV, DEFAULT_NOTICE_MODEL),
        max_tokens=1500,
        output_config={"effort": "low"},
        system=(
            "You write correction notices for the TTB labeling division. Rewrite the notice "
            "below in plain, courteous English for a small business owner. Keep every factual "
            "finding, every quoted value, and the regulatory citation exactly as given. Keep the "
            "numbered list. Do not add findings, apologies, or legal threats. Return only the letter."
        ),
        messages=[{"role": "user", "content": template}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"unexpected stop reason {response.stop_reason}")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise RuntimeError("empty rewrite")
    return text
