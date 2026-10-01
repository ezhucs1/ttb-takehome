"""The command line reads a label set of one or more panels with the configured reader."""

from __future__ import annotations

import json

import pytest

from labelverify.cli import main
from labelverify.engine.extractors.demo import SAMPLES_DIR, load_manifest


def _sample_path(sample_id: str) -> str:
    return str(SAMPLES_DIR / next(s for s in load_manifest() if s["id"] == sample_id)["file"])


def test_extract_reads_several_panels_as_one_set(capsys, monkeypatch):
    monkeypatch.setenv("LABELVERIFY_EXTRACTOR", "demo")
    front = _sample_path("old-tom-bourbon")
    assert main(["extract", front]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["brand_name"]["value"] == "OLD TOM DISTILLERY"
    # Two panels go to the reader in one call; the demo reader keys on the first panel.
    assert main(["extract", front, front]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["brand_name"]["value"] == "OLD TOM DISTILLERY"
    assert "for 2 image(s)" in captured.err


def test_missing_file_is_a_friendly_error(monkeypatch):
    monkeypatch.setenv("LABELVERIFY_EXTRACTOR", "demo")
    with pytest.raises(SystemExit) as stop:
        main(["extract", "no-such-label.jpg"])
    assert "no-such-label.jpg" in str(stop.value) and "hint" in str(stop.value)


def test_extract_reports_panel_sizes_and_honours_timeout_flag(capsys, monkeypatch):
    import os

    monkeypatch.setenv("LABELVERIFY_EXTRACT_TIMEOUT", "20")  # restored after the test
    front = str(SAMPLES_DIR / "old-tom-bourbon.jpg")
    assert main(["extract", front, "--extractor", "demo", "--timeout", "90"]) == 0
    err = capsys.readouterr().err
    assert "# panel 1:" in err and "KB sent" in err
    assert os.environ["LABELVERIFY_EXTRACT_TIMEOUT"] == "90.0"
