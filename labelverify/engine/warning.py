"""Government Health Warning Statement rules (27 CFR Part 16).

The statement must appear word for word, and the words "GOVERNMENT WARNING" must be
in capital letters and bold type. The body is compared word for word after
normalization; the heading is checked separately for capitalization and bold.
"""

from __future__ import annotations

import difflib
import re
from functools import lru_cache

from .models import FieldResult, HealthWarningExtraction, Verdict, WordDiff
from .normalize import collapse_whitespace, normalize_words

HEADING = "GOVERNMENT WARNING:"

STATUTORY_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic beverages "
    "during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic "
    "beverages impairs your ability to drive a car or operate machinery, and may cause "
    "health problems."
)

STATUTORY_TEXT = f"{HEADING} {STATUTORY_BODY}"

_HEADING_WORDS = ["government", "warning"]
_HEADING_RE = re.compile(r"government\s+warning", re.IGNORECASE)


def split_heading(text: str) -> tuple[str, str]:
    """Return (heading_as_printed, body_as_printed) from a transcribed statement."""
    cleaned = collapse_whitespace(text)
    match = _HEADING_RE.search(cleaned)  # searched in place: case folding can change lengths
    if match is None:
        return "", cleaned
    idx, end = match.span()
    if end < len(cleaned) and cleaned[end] == ":":
        end += 1
    return cleaned[idx:end], cleaned[end:].strip()


def word_diff(expected: str, actual: str) -> list[WordDiff]:
    """Word-level diff between the statutory body and the label body, for display."""
    expected_tokens = _tokens(expected)
    actual_tokens = _tokens(actual)
    expected_words = [t for t, _ in expected_tokens]
    actual_words = [t for t, _ in actual_tokens]
    matcher = difflib.SequenceMatcher(a=expected_words, b=actual_words, autojunk=False)
    diff: list[WordDiff] = []

    def add(op: str, tokens: list[tuple[str, str]]) -> None:
        diff.extend(WordDiff(op=op, text=t, display=d) for t, d in tokens)

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            add("equal", actual_tokens[j1:j2])  # show the label's own words when they match
        elif op == "delete":
            add("missing", expected_tokens[i1:i2])
        elif op == "insert":
            add("extra", actual_tokens[j1:j2])
        else:  # replace
            add("missing", expected_tokens[i1:i2])
            add("extra", actual_tokens[j1:j2])
    return diff


_LINE_HYPHEN_RE = re.compile(r"(?<=\w)-\s+(?=\w)")
_STATUTORY_WORDS = frozenset(normalize_words(STATUTORY_TEXT))


@lru_cache(maxsize=256)
def _tokens(text: str) -> list[tuple[str, str]]:
    """(normalized token, word as printed) pairs; punctuation-only words are dropped. A word
    hyphenated across a line break ("BE- CAUSE", "CONSUM- PTION") is one word: the statutory
    text has no hyphens, so a hyphen at a break is never part of a word."""
    pairs: list[tuple[str, str]] = []
    for raw in _LINE_HYPHEN_RE.sub("", collapse_whitespace(text)).split():
        if "-" in raw and normalize_words(raw.replace("-", "")) in (
            [word] for word in _STATUTORY_WORDS
        ):
            raw = raw.replace("-", "")  # "BE-CAUSE": a line break transcribed without the space
        normalized = normalize_words(raw)
        if not normalized:
            continue
        if len(normalized) == 1:
            pairs.append((normalized[0], raw))
        else:  # e.g. "car/truck" normalizes to two tokens; show each as itself
            pairs.extend((token, token) for token in normalized)
    return pairs


def check_health_warning(extraction: HealthWarningExtraction) -> FieldResult:
    base = dict(
        field="health_warning",
        label="Government Health Warning",
        application_value=STATUTORY_TEXT,
        confidence=extraction.confidence,
    )

    if not extraction.present or not extraction.text:
        return FieldResult(
            verdict=Verdict.MISMATCH,
            label_value=None,
            reason="No Government Health Warning Statement was found on the label.",
            **base,
        )

    heading, body = split_heading(extraction.text)
    notes: list[str] = []
    problems: list[str] = []
    review_reasons: list[str] = []

    # Body: word for word after normalization.
    diff = word_diff(STATUTORY_BODY, body)
    wrong_words = [d for d in diff if d.op != "equal"]
    if not _tokens(body):
        problems.append("The warning heading is present but the statement body is missing.")
    elif wrong_words:
        missing = sum(1 for d in wrong_words if d.op == "missing")
        extra = sum(1 for d in wrong_words if d.op == "extra")
        problems.append(
            f"Statement text differs from the required wording ({missing} required word(s) "
            f"missing, {extra} unexpected word(s))."
        )

    # Heading: must exist, must be all caps, should be bold.
    if not heading:
        problems.append("The statement does not begin with 'GOVERNMENT WARNING:'.")
    else:
        if not heading.endswith(":"):
            notes.append("Heading is missing the trailing colon.")
        heading_text = heading.rstrip(":")
        heading_is_caps = heading_text == heading_text.upper()
        if extraction.heading_all_caps is False or not heading_is_caps:
            problems.append(
                f"'GOVERNMENT WARNING' must be in capital letters (label shows '{heading}')."
            )
        # Bold type is required (27 CFR 16.22, and the brief says so in as many words). A
        # reader that says "not bold" is evidence, even though on real labels it misjudged
        # the weight more often than not, so that goes to a person with the image beside
        # the table. A reader that cannot tell is not evidence of anything: a note only.
        if extraction.heading_bold is False:
            review_reasons.append(
                "The reader did not see the heading as bold. Bold type is required; confirm "
                "the weight on the image."
            )
        elif extraction.heading_bold is None:
            notes.append(
                "The reader could not tell whether the heading is bold; confirm on the image."
            )

    if problems:
        verdict = Verdict.MISMATCH
        reason = " ".join(problems)
    elif review_reasons:
        verdict = Verdict.NEEDS_REVIEW
        reason = " ".join(review_reasons)
    else:
        verdict = Verdict.MATCH
        reason = (
            "Statement matches the required wording; heading is capitalized and bold."
            if extraction.heading_bold
            else "Statement matches the required wording; heading is capitalized."
        )

    return FieldResult(
        verdict=verdict,
        label_value=collapse_whitespace(extraction.text),
        reason=reason,
        notes=notes + (review_reasons if problems else []),
        diff=diff,
        **base,
    )
