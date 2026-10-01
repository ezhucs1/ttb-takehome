"""A daily cap on model reads, for deployments on a public URL.

Anyone who can reach the demo can upload labels, and every upload is a paid model call.
``BudgetedExtractor`` wraps a real extractor and refuses, with a clear message, once the
day's allowance is spent. The count is per process and resets at midnight UTC; that is
enough to bound a day's spend without adding a datastore. Demo and fixture extractors
are never wrapped because they cost nothing.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from datetime import UTC, datetime

from ..models import LabelExtraction
from .base import ExtractionError, Panel

LIMIT_ENV = "LABELVERIFY_DAILY_READ_LIMIT"
FREE_EXTRACTORS = ("demo", "fixture")


def configured_limit() -> int:
    """Reads per UTC day allowed by the environment; 0 means unlimited."""
    raw = os.environ.get(LIMIT_ENV, "").strip()
    return int(raw) if raw.isdigit() else 0


class BudgetExhausted(ExtractionError):
    """Raised when the day's allowance of model reads has been used."""


class BudgetedExtractor:
    """Delegates to ``inner`` until ``limit`` reads have been made today."""

    def __init__(self, inner, limit: int, *, clock=None):
        self.inner = inner
        self.limit = limit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._day = self._today()
        self._used = 0

    @property
    def name(self) -> str:
        return self.inner.name

    def _today(self) -> str:
        return self._clock().strftime("%Y-%m-%d")

    @property
    def used_today(self) -> int:
        with self._lock:
            self._roll()
            return self._used

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used_today, 0)

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self._used = today, 0

    def _charge(self) -> None:
        with self._lock:
            self._roll()
            if self._used >= self.limit:
                raise BudgetExhausted(
                    f"Today's reading budget of {self.limit} labels is used up; the demo "
                    "reopens at midnight UTC. The bundled sample labels still work."
                )
            self._used += 1

    def extract(self, image: bytes, media_type: str) -> LabelExtraction:
        self._charge()
        return self.inner.extract(image, media_type)

    def extract_panels(self, panels: Sequence[Panel]) -> LabelExtraction:
        self._charge()
        return self.inner.extract_panels(panels)


def with_budget(extractor, limit: int | None = None):
    """Wrap ``extractor`` when a limit is configured and it is not a free one."""
    limit = configured_limit() if limit is None else limit
    if limit <= 0 or getattr(extractor, "name", "") in FREE_EXTRACTORS:
        return extractor
    return BudgetedExtractor(extractor, limit)
