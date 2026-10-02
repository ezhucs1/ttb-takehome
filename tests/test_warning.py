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


def test_words_hyphenated_across_a_line_break_are_one_word():
    from labelverify.engine.models import HealthWarningExtraction, Verdict
    from labelverify.engine.warning import STATUTORY_TEXT, check_health_warning

    text = STATUTORY_TEXT.replace("because", "be- cause").replace("Consumption", "Consum- ption")
    result = check_health_warning(
        HealthWarningExtraction(
            present=True, text=text, heading_all_caps=True, heading_bold=True, confidence=0.9
        )
    )
    assert result.verdict is Verdict.MATCH


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


def test_not_bold_goes_to_a_person_but_unknown_is_only_a_note():
    """The brief requires a bold heading, so a reader saying "not bold" is a review item;
    a reader that cannot tell gives no evidence either way."""
    result = check_health_warning(warning(STATUTORY_TEXT, bold=False))
    assert result.verdict is Verdict.NEEDS_REVIEW
    assert "did not see the heading as bold" in result.reason
    unknown = check_health_warning(warning(STATUTORY_TEXT, bold=None))
    assert unknown.verdict is Verdict.MATCH
    assert any("could not tell" in n for n in unknown.notes)


def test_a_hyphen_inside_a_statutory_word_is_a_line_break():
    text = STATUTORY_TEXT.replace("because", "BE-CAUSE").replace("Consumption", "CONSUM-PTION")
    assert check_health_warning(warning(text)).verdict is Verdict.MATCH
    typo = STATUTORY_TEXT.replace("ability", "ABILILTY")
    assert check_health_warning(warning(typo)).verdict is Verdict.MISMATCH


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


def test_word_diff_keeps_words_as_printed_for_display():
    printed = STATUTORY_BODY.upper().replace("MAY CAUSE HEALTH PROBLEMS", "CAN CAUSE HEALTH ISSUES")
    diff = word_diff(STATUTORY_BODY, printed)
    assert diff[0].display == "(1)" and diff[0].text == "1"
    assert [d.display for d in diff if d.op == "extra"] == ["CAN", "ISSUES."]
    assert [d.display for d in diff if d.op == "missing"] == ["may", "problems."]
    assert any(d.display == "ACCORDING" for d in diff if d.op == "equal")
