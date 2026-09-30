"""Extractor interface.

An extractor turns one or more label images (front, back, neck panels of the same
product) into a single ``LabelExtraction``. The comparison engine does not care which
extractor ran, which is what lets the app fall back to a local OCR pipeline when the
cloud model is unreachable.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..models import LabelExtraction

Panel = tuple[bytes, str]  # (image bytes, media type)


class ExtractionError(RuntimeError):
    """The extractor could not produce a result (network, timeout, refusal, missing binary)."""


class Extractor(Protocol):
    name: str

    def extract(self, image: bytes, media_type: str) -> LabelExtraction:
        """Read one label image."""
        ...

    def extract_panels(self, panels: Sequence[Panel]) -> LabelExtraction:
        """Read several images of the same label set and report each field once."""
        ...


class FixtureExtractor:
    """Returns a canned extraction. Used by tests."""

    name = "fixture"

    def __init__(self, extraction: LabelExtraction):
        self._extraction = extraction
        self.calls: list[list[Panel]] = []

    def extract(self, image: bytes, media_type: str) -> LabelExtraction:
        return self.extract_panels([(image, media_type)])

    def extract_panels(self, panels: Sequence[Panel]) -> LabelExtraction:
        self.calls.append(list(panels))
        return self._extraction.model_copy(deep=True)
