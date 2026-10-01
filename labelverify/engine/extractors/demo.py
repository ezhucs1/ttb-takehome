"""Offline demo extractor.

Returns the ground-truth extraction for the bundled sample labels so the whole
workflow can be exercised with no API key and no OCR binary. It recognizes a sample
by hashing the prepared image bytes; anything else raises a clear error rather than
inventing a result.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from ..models import LabelExtraction
from ..preprocess import prepare_image
from .base import ExtractionError, Panel

SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "samples"


def load_manifest(samples_dir: Path = SAMPLES_DIR) -> list[dict]:
    path = samples_dir / "manifest.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


@lru_cache(maxsize=4)
def _hash_table(samples_dir: Path) -> dict[str, LabelExtraction]:
    """Digest -> extraction for every bundled sample, as uploaded and as preprocessed.
    Hashing and re-encoding the samples takes about half a second, so it happens once
    per process rather than once per extractor."""
    table: dict[str, LabelExtraction] = {}
    for entry in load_manifest(samples_dir):
        raw = (samples_dir / entry["file"]).read_bytes()
        extraction = LabelExtraction.model_validate(entry["extraction"])
        table[_digest(raw)] = extraction
        table[_digest(prepare_image(raw).data)] = extraction
    return table


class DemoExtractor:
    name = "demo"

    def __init__(self, samples_dir: Path = SAMPLES_DIR):
        self.samples_dir = samples_dir
        self._by_hash = _hash_table(Path(samples_dir))

    def extract(self, image: bytes, media_type: str) -> LabelExtraction:
        return self.extract_panels([(image, media_type)])

    def extract_panels(self, panels: Sequence[Panel]) -> LabelExtraction:
        """The first panel that is a known sample wins; extra panels are ignored."""
        for image, _ in panels:
            extraction = self._by_hash.get(_digest(image))
            if extraction is not None:
                return extraction.model_copy(deep=True)
        raise ExtractionError(
            "Demo mode can only read the bundled sample labels. "
            "Set ANTHROPIC_API_KEY to verify your own images."
        )


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
