"""Extractor registry.

``get_extractor()`` picks the configured backend from ``LABELVERIFY_EXTRACTOR``:

* ``claude``: Anthropic vision model (default when ANTHROPIC_API_KEY is set)
* ``gemini``: Google Gemini vision model (default when only GEMINI_API_KEY is set)
* ``tesseract``: local OCR fallback
* ``demo``: canned results for the bundled sample labels (default when no key is set)
"""

from __future__ import annotations

import os

from .base import ExtractionError, Extractor, FixtureExtractor
from .budget import BudgetedExtractor, BudgetExhausted, with_budget
from .claude import ClaudeExtractor
from .demo import DemoExtractor
from .gemini import GeminiExtractor
from .tesseract import TesseractExtractor

__all__ = [
    "BudgetExhausted",
    "BudgetedExtractor",
    "ClaudeExtractor",
    "DemoExtractor",
    "ExtractionError",
    "Extractor",
    "FixtureExtractor",
    "GeminiExtractor",
    "TesseractExtractor",
    "get_extractor",
    "resolve_extractor_name",
    "with_budget",
]

_REGISTRY: dict[str, type] = {
    "claude": ClaudeExtractor,
    "gemini": GeminiExtractor,
    "tesseract": TesseractExtractor,
    "demo": DemoExtractor,
}


def resolve_extractor_name(name: str | None = None) -> str:
    """Explicit choice wins; otherwise whichever provider has a key, else demo."""
    chosen = (name or os.environ.get("LABELVERIFY_EXTRACTOR") or "").strip().lower()
    if chosen:
        return chosen
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return "demo"


def get_extractor(name: str | None = None) -> Extractor:
    """The configured extractor, wrapped in the daily read budget when one is set."""
    chosen = resolve_extractor_name(name)
    factory = _REGISTRY.get(chosen)
    if factory is None:
        raise ValueError(
            f"Unknown extractor '{chosen}'. Choose one of: {', '.join(sorted(_REGISTRY))}."
        )
    return with_budget(factory())  # a KeyError inside the constructor stays a KeyError
