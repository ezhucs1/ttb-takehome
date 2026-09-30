import pytest

from labelverify.engine.extractors import FixtureExtractor
from labelverify.engine.extractors.base import ExtractionError
from labelverify.engine.models import Recommendation
from labelverify.engine.preprocess import UnreadableImageError
from labelverify.engine.verify import run_verification


class FailingExtractor:
    name = "failing"

    def extract(self, image, media_type):
        raise ExtractionError("simulated outage")


class RecordingExtractor(FixtureExtractor):
    def __init__(self, extraction):
        super().__init__(extraction)
        self.received: list[tuple[int, str]] = []

    def extract(self, image, media_type):
        self.received.append((len(image), media_type))
        return super().extract(image, media_type)


def test_end_to_end_with_fixture_extractor(label_png, application, extraction):
    extractor = RecordingExtractor(extraction)
    result = run_verification(label_png, application, extractor=extractor)

    assert result.recommendation is Recommendation.APPROVE
    assert result.extractor == "fixture"
    assert result.total_ms >= result.extraction_ms >= 0
    size, media_type = extractor.received[0]
    assert media_type == "image/jpeg"  # preprocessing re-encoded the PNG
    assert size > 0 and size != len(label_png)


def test_prepare_can_be_skipped(label_png, application, extraction):
    extractor = RecordingExtractor(extraction)
    run_verification(label_png, application, extractor=extractor, prepare=False)
    assert extractor.received[0] == (len(label_png), "image/jpeg")


def test_fallback_runs_when_primary_fails(label_png, application, extraction):
    result = run_verification(
        label_png,
        application,
        extractor=FailingExtractor(),
        fallback=FixtureExtractor(extraction),
    )
    assert result.recommendation is Recommendation.APPROVE
    assert result.extractor == "fixture (fallback after failing failed)"


def test_primary_failure_propagates_without_fallback(label_png, application):
    with pytest.raises(ExtractionError, match="simulated outage"):
        run_verification(label_png, application, extractor=FailingExtractor())


def test_unreadable_upload_is_rejected_before_extraction(application, extraction):
    extractor = RecordingExtractor(extraction)
    with pytest.raises(UnreadableImageError):
        run_verification(b"not an image", application, extractor=extractor)
    assert extractor.received == []
