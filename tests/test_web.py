"""Web layer tests using FastAPI's TestClient with an injected extractor."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from labelverify.engine.extractors import DemoExtractor, FixtureExtractor
from labelverify.engine.extractors.base import ExtractionError
from labelverify.engine.extractors.demo import load_manifest
from labelverify.web.app import create_app

FORM = {
    "beverage_type": "distilled_spirits",
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "producer_name": "Old Tom Distillery",
    "producer_address": "123 Barrel Lane, Bardstown, Kentucky 40004",
}


class BrokenExtractor:
    name = "broken"

    def extract(self, image, media_type):
        raise ExtractionError("model unreachable")


@pytest.fixture
def client(extraction):
    return TestClient(create_app(extractor=FixtureExtractor(extraction)))


def test_index_renders_form_and_samples(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'id="verify-form"' in resp.text
    assert "Test fixture" in resp.text
    assert "old-tom-bourbon" in resp.text


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok", "extractor": "Test fixture"}


def test_sample_image_is_served(client):
    resp = client.get("/samples/old-tom-bourbon/image")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert client.get("/samples/nope/image").status_code == 404


def test_verify_with_upload_returns_result_partial(client, label_png):
    resp = client.post("/verify", data=FORM, files={"image": ("label.png", label_png, "image/png")})
    assert resp.status_code == 200
    assert "Recommend approval" in resp.text
    assert "Government Health Warning" in resp.text
    assert 'class="badge badge-match"' in resp.text


def test_verify_with_sample_id_uses_bundled_image(client):
    resp = client.post("/verify", data={**FORM, "sample_id": "old-tom-bourbon"})
    assert resp.status_code == 200
    assert "Recommend approval" in resp.text


def test_verify_without_image_is_a_clear_error(client):
    resp = client.post("/verify", data=FORM)
    assert resp.status_code == 400
    assert "upload a label image" in resp.text


def test_verify_with_garbage_upload(client):
    resp = client.post("/verify", data=FORM, files={"image": ("x.png", b"nope", "image/png")})
    assert resp.status_code == 400
    assert "not a readable image" in resp.text


def test_verify_with_invalid_beverage_type(client, label_png):
    resp = client.post(
        "/verify",
        data={**FORM, "beverage_type": "mead"},
        files={"image": ("label.png", label_png, "image/png")},
    )
    assert resp.status_code == 422
    assert "invalid" in resp.text


def test_extraction_failure_is_reported_not_crashed(label_png):
    client = TestClient(create_app(extractor=BrokenExtractor()))
    resp = client.post("/verify", data=FORM, files={"image": ("label.png", label_png, "image/png")})
    assert resp.status_code == 502
    assert "model unreachable" in resp.text


def test_json_api(client, label_png):
    resp = client.post(
        "/api/verify",
        data={"application": json.dumps(FORM)},
        files={"image": ("label.png", label_png, "image/png")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommendation"] == "approve"
    assert len(body["fields"]) == 8
    assert body["source"] == "upload:label.png"


def test_json_api_rejects_bad_application(client, label_png):
    resp = client.post(
        "/api/verify",
        data={"application": "{}"},
        files={"image": ("label.png", label_png, "image/png")},
    )
    assert resp.status_code == 422


class TestDemoMode:
    """Every bundled sample must produce the recommendation its manifest promises."""

    @pytest.mark.parametrize("sample", load_manifest(), ids=lambda s: s["id"])
    def test_sample_gives_expected_recommendation(self, sample):
        client = TestClient(create_app(extractor=DemoExtractor()))
        resp = client.post(
            "/api/verify",
            data={"application": json.dumps(sample["application"]), "sample_id": sample["id"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["recommendation"] == sample["expected"]

    def test_unknown_image_is_refused_with_guidance(self, label_png):
        client = TestClient(create_app(extractor=DemoExtractor()))
        resp = client.post(
            "/verify", data=FORM, files={"image": ("label.png", label_png, "image/png")}
        )
        assert resp.status_code == 502
        assert "ANTHROPIC_API_KEY" in resp.text
