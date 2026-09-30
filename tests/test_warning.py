from labelverify.engine.models import HealthWarningExtraction, Verdict
from labelverify.engine.warning import (
    STATUTORY_BODY,
    STATUTORY_TEXT,
    check_health_warning,
    split_heading,
    word_diff,
)


def warning(text, *, caps=True, bold=True, present=True, confidence=0.95):
    return HealthWarningExtraction(
        present=present,
        text=text,
        heading_all_caps=caps,
        heading_bold=bold,
        confidence=confidence,
    )


def test_exact_statement_matches():
    result = check_health_warning(warning(STATUTORY_TEXT))
    assert result.verdict is Verdict.MATCH
    assert all(d.op == "equal" for d in result.diff)


def test_body_in_all_caps_still_matches():
    result = check_health_warning(warning(STATUTORY_TEXT.upper()))
    assert result.verdict is Verdict.MATCH


def test_line_breaks_and_extra_spaces_are_ignored():
    wrapped = STATUTORY_TEXT.replace(" during ", "\nduring ").replace(". (2)", ".   (2)")
    assert check_health_warning(warning(wrapped)).verdict is Verdict.MATCH


def test_missing_statement_is_a_mismatch():
    result = check_health_warning(warning(None, present=False))
    assert result.verdict is Verdict.MISMATCH
    assert "No Government Health Warning" in result.reason


def test_title_case_heading_is_rejected():
    text = STATUTORY_TEXT.replace("GOVERNMENT WARNING", "Government Warning")
    result = check_health_warning(warning(text, caps=False))
    assert result.verdict is Verdict.MISMATCH
    assert "capital letters" in result.reason


def test_title_case_heading_detected_from_text_even_if_model_says_caps():
    text = STATUTORY_TEXT.replace("GOVERNMENT WARNING", "Government Warning")
    result = check_health_warning(warning(text, caps=True))
    assert result.verdict is Verdict.MISMATCH


def test_altered_wording_is_a_mismatch_with_word_diff():
    text = STATUTORY_TEXT.replace("may cause health problems", "can cause health issues")
    result = check_health_warning(warning(text))
    assert result.verdict is Verdict.MISMATCH
    missing = [d.text for d in result.diff if d.op == "missing"]
    extra = [d.text for d in result.diff if d.op == "extra"]
    assert missing == ["may", "problems"]
    assert extra == ["can", "issues"]
    assert "2 required word(s) missing" in result.reason


def test_truncated_statement_is_a_mismatch():
    text = STATUTORY_TEXT.split("(2)")[0].strip()
    result = check_health_warning(warning(text))
    assert result.verdict is Verdict.MISMATCH
    assert any(d.op == "missing" for d in result.diff)


def test_heading_only_is_a_mismatch():
    result = check_health_warning(warning("GOVERNMENT WARNING:"))
    assert result.verdict is Verdict.MISMATCH
    assert "body is missing" in result.reason


def test_not_bold_heading_needs_review_not_rejection():
    result = check_health_warning(warning(STATUTORY_TEXT, bold=False))
    assert result.verdict is Verdict.NEEDS_REVIEW
    assert "bold" in result.reason


def test_unknown_bold_needs_review():
    result = check_health_warning(warning(STATUTORY_TEXT, bold=None))
    assert result.verdict is Verdict.NEEDS_REVIEW


def test_missing_colon_is_only_a_note():
    text = STATUTORY_TEXT.replace("WARNING:", "WARNING")
    result = check_health_warning(warning(text))
    assert result.verdict is Verdict.MATCH
    assert any("colon" in n for n in result.notes)


def test_split_heading():
    heading, body = split_heading("GOVERNMENT WARNING: (1) According ...")
    assert heading == "GOVERNMENT WARNING:"
    assert body == "(1) According ..."
    assert split_heading("no heading here") == ("", "no heading here")


def test_word_diff_identical_bodies():
    assert all(d.op == "equal" for d in word_diff(STATUTORY_BODY, STATUTORY_BODY))
