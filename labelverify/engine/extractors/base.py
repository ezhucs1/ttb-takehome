"""Extractor interface.

An extractor turns label image bytes into a ``LabelExtraction``. The comparison engine
does not care which one ran, which is what lets the app fall back to a local OCR
pipeline when the cloud model is unreachable.
"""

from __future__ import annotations

from typing import Protocol

from ..models import LabelExtraction


class ExtractionError(RuntimeError):
    """The extractor could not produce a result (network, timeout, refusal, missing binary)."""


class Extractor(Protocol):
    name: str

    def extract(self, image: bytes, media_type: str) -> LabelExtraction: ...


class FixtureExtractor:
    """Returns a canned extraction. Used by tests and by the offline demo mode."""

    name = "fixture"

    def __init__(self, extraction: LabelExtraction):
        self._extraction = extraction

    def extract(self, image: bytes, media_type: str) -> LabelExtraction:
        return self._extraction.model_copy(deep=True)
