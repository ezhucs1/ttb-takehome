"""Extractor registry.

``get_extractor()`` picks the configured backend from ``LABELVERIFY_EXTRACTOR``:
``claude`` (default, vision model) or ``tesseract`` (local OCR fallback).
"""

from __future__ import annotations

import os

from .base import ExtractionError, Extractor, FixtureExtractor
from .claude import ClaudeExtractor
from .tesseract import TesseractExtractor

__all__ = [
    "ClaudeExtractor",
    "ExtractionError",
    "Extractor",
    "FixtureExtractor",
    "TesseractExtractor",
    "get_extractor",
]

_REGISTRY: dict[str, type] = {
    "claude": ClaudeExtractor,
    "tesseract": TesseractExtractor,
}


def get_extractor(name: str | None = None) -> Extractor:
    chosen = (name or os.environ.get("LABELVERIFY_EXTRACTOR") or "claude").strip().lower()
    try:
        return _REGISTRY[chosen]()
    except KeyError as exc:
        raise ValueError(
            f"Unknown extractor '{chosen}'. Choose one of: {', '.join(sorted(_REGISTRY))}."
        ) from exc
