"""Offline demo extractor.

Returns the ground-truth extraction for the bundled sample labels so the whole
workflow can be exercised with no API key and no OCR binary. It recognizes a sample
by hashing the prepared image bytes; anything else raises a clear error rather than
inventing a result.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..models import LabelExtraction
from ..preprocess import prepare_image
from .base import ExtractionError

SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "samples"


def load_manifest(samples_dir: Path = SAMPLES_DIR) -> list[dict]:
    path = samples_dir / "manifest.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


class DemoExtractor:
    name = "demo"

    def __init__(self, samples_dir: Path = SAMPLES_DIR):
        self.samples_dir = samples_dir
        self._by_hash: dict[str, LabelExtraction] = {}
        for entry in load_manifest(samples_dir):
            raw = (samples_dir / entry["file"]).read_bytes()
            extraction = LabelExtraction.model_validate(entry["extraction"])
            self._by_hash[_digest(raw)] = extraction
            self._by_hash[_digest(prepare_image(raw).data)] = extraction

    def extract(self, image: bytes, media_type: str) -> LabelExtraction:
        extraction = self._by_hash.get(_digest(image))
        if extraction is None:
            raise ExtractionError(
                "Demo mode can only read the bundled sample labels. "
                "Set ANTHROPIC_API_KEY to verify your own images."
            )
        return extraction.model_copy(deep=True)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
