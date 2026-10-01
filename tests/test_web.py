"""HTTP-level tests for the applicant and specialist workflows."""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

from labelverify.engine.extractors import DemoExtractor, FixtureExtractor
from labelverify.engine.extractors.demo import SAMPLES_DIR, load_manifest
from labelverify.web.app import create_app

SPECIALIST = {"email": "sarah.chen@ttb.gov", "password": "labelverify"}
APPLICANT = {"email": "maria@alvarezlabels.com", "password": "labelverify"}

FORM = {
    "beverage_type": "distilled_spirits",
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "producer_name": "Old Tom Distillery",
    "producer_address": "Bardstown, KY 40004",
}


def sample(sample_id: str) -> dict:
    return next(s for s in load_manifest() if s["id"] == sample_id)


def sample_bytes(sample_id: str) -> bytes:
    return (SAMPLES_DIR / sample(sample_id)["file"]).read_bytes()


@pytest.fixture
def app(tmp_path):
    return create_app(
        extractor=DemoExtractor(), database_url=f"sqlite:///{tmp_path}/web.db", secret="test-secret"
    )


@pytest.fixture
def anon(app):
    return TestClient(app)


def login(app, creds) -> TestClient:
    client = TestClient(app)
    resp = client.post("/login", data=creds, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return client


@pytest.fixture
def specialist(app):
    return login(app, SPECIALIST)


@pytest.fixture
def applicant(app):
    return login(app, APPLICANT)


def other_applicant(app) -> dict:
    """A second applicant account, created on demand: the demo seeds only one."""
    from labelverify.web.seed import ensure_user

    with app.state.session_factory() as db:
        ensure_user(
            db,
            email="compliance@stonesthrow.wine",
            name="Devin Okafor",
            role="applicant",
            organization="Stone's Throw Vineyards",
        )
        db.commit()
    return {"email": "compliance@stonesthrow.wine", "password": "labelverify"}


def ids_in(html: str, prefix: str) -> list[str]:
    return list(dict.fromkeys(re.findall(rf"{prefix}/([0-9a-f]{{32}})", html)))


class TestAuth:
    def test_anonymous_is_redirected_to_login(self, anon):
        resp = anon.get("/specialist", follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"].startswith("/login")
        assert anon.get("/").headers.get("content-type", "").startswith("text/html")

    def test_login_ignores_a_next_url_from_the_other_role(self, app):
        """A session that expired on an applicant page must not send a specialist there."""
        client = TestClient(app)
        resp = client.post(
            "/login",
            data={**SPECIALIST, "next": "/applicant/applications/abc"},
            follow_redirects=False,
        )
        assert resp.headers["location"] == "/specialist"
        resp = client.post("/login", data={**SPECIALIST, "next": "/inbox"}, follow_redirects=False)
        assert resp.headers["location"] == "/inbox"
        resp = client.post(
            "/login", data={**SPECIALIST, "next": "//evil.example"}, follow_redirects=False
        )
        assert resp.headers["location"] == "/specialist"

    def test_wrong_role_page_explains_itself(self, specialist):
        resp = specialist.get("/applicant")
        assert resp.status_code == 403 and "Not your page" in resp.text
        assert "signed in as Sarah Chen" in resp.text and 'href="/"' in resp.text

    def test_landing_page_has_the_sign_in_dialog_and_versioned_assets(self, anon):
        page = anon.get("/login").text
        assert '<dialog id="signin"' in page and "data-open-signin" in page
        assert "Not an official" in page  # the prototype disclaimer next to the seal
        assert re.search(r'/static/app\.css\?v=[0-9a-f]{10}', page)
        assert "Sarah Chen" in page and 'data-open' not in page.split("<dialog")[1].split(">")[0]

    def test_failed_login_reopens_the_dialog(self, anon):
        resp = anon.post("/login", data={"email": SPECIALIST["email"], "password": "nope"})
        assert resp.status_code == 401
        assert '<dialog id="signin" class="signin" data-open>' in resp.text

    def test_demo_accounts_can_be_hidden_for_a_public_deployment(self, anon, monkeypatch):
        monkeypatch.setenv("LABELVERIFY_DEMO_ACCOUNTS", "false")
        page = anon.get("/login").text
        assert "Sarah Chen" not in page and "labelverify</code>" not in page
        assert "project README" in page and "github.com/ezhucs1/ttb-takehome" in page
        # Hiding the list does not disable the accounts themselves.
        assert anon.post("/login", data=SPECIALIST, follow_redirects=False).status_code == 303

    def test_bad_password(self, anon):
        resp = anon.post("/login", data={"email": SPECIALIST["email"], "password": "nope"})
        assert resp.status_code == 401 and "do not match" in resp.text

    def test_roles_are_enforced(self, specialist, applicant):
        assert specialist.get("/applicant").status_code == 403
        assert applicant.get("/specialist").status_code == 403

    def test_home_routes_by_role(self, specialist, applicant):
        assert specialist.get("/", follow_redirects=False).headers["location"] == "/specialist"
        assert applicant.get("/", follow_redirects=False).headers["location"] == "/applicant"

    def test_logout(self, specialist):
        specialist.post("/logout", follow_redirects=False)
        assert specialist.get("/specialist", follow_redirects=False).status_code == 303

    def test_healthz(self, anon):
        assert anon.get("/healthz").json()["extractor"] == "Demo mode"


class TestSpecialistWorkflow:
    def test_queue_renders_seeded_applications_and_stats(self, specialist):
        resp = specialist.get("/specialist")
        assert resp.status_code == 200
        assert "Ready" in resp.text and "COLA-" in resp.text
        for tab in ("ready", "review", "corrections", "approved", "bogus"):
            assert specialist.get(f"/specialist?tab={tab}").status_code == 200

    def test_opening_a_submitted_application_claims_it(self, specialist):
        app_id = ids_in(specialist.get("/specialist?tab=ready").text, "/specialist/applications")[0]
        resp = specialist.get(f"/specialist/applications/{app_id}")
        assert resp.status_code == 200
        assert "Under review" in resp.text and "Review started by Sarah Chen" in resp.text
        assert "Approve label" in resp.text

    def test_draft_notice_then_request_correction(self, specialist, applicant):
        app_id = ids_in(specialist.get("/specialist?tab=review").text, "/specialist/applications")[
            0
        ]
        draft = specialist.post(
            f"/specialist/applications/{app_id}/notice", headers={"X-Partial": "1"}
        )
        assert (
            draft.status_code == 200
            and "<textarea" in draft.text
            and "Re: COLA application" in draft.text
        )
        body = re.search(r"<textarea[^>]*>(.*?)</textarea>", draft.text, re.S).group(1)
        resp = specialist.post(
            f"/specialist/applications/{app_id}/decision",
            data={"action": "request_correction", "notice_body": body, "notice_source": "template"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        page = specialist.get(f"/specialist/applications/{app_id}")
        assert "Correction requested" in page.text and "Correction request" in page.text
        # No decision panel while the applicant holds the next move; threads stay open.
        assert "Waiting on the applicant" in page.text and "Approve label" not in page.text
        assert "comment-form" in page.text
        # The applicant now sees it as needing action, with the notice and field comments.
        dash = applicant.get("/applicant")
        assert "Corrections requested" in dash.text or "Correction requested" in dash.text

    def test_drafted_notice_can_be_discarded(self, specialist):
        app_id = ids_in(specialist.get("/specialist?tab=review").text, "/specialist/applications")[
            0
        ]
        draft = specialist.post(
            f"/specialist/applications/{app_id}/notice", headers={"X-Partial": "1"}
        )
        assert draft.status_code == 200 and "data-notice-discard" in draft.text

    def test_correction_without_notice_is_rejected(self, specialist):
        app_id = ids_in(specialist.get("/specialist?tab=review").text, "/specialist/applications")[
            0
        ]
        resp = specialist.post(
            f"/specialist/applications/{app_id}/decision",
            data={"action": "request_correction"},
            headers={"X-Partial": "1"},
        )
        assert resp.status_code == 422 and "needs a notice" in resp.text

    def test_approve_and_reject(self, specialist):
        ready = ids_in(specialist.get("/specialist?tab=ready").text, "/specialist/applications")
        assert len(ready) >= 2
        assert (
            specialist.post(
                f"/specialist/applications/{ready[0]}/decision",
                data={"action": "approve"},
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert (
            specialist.post(
                f"/specialist/applications/{ready[1]}/decision",
                data={"action": "reject", "notice_body": "Not eligible."},
                follow_redirects=False,
            ).status_code
            == 303
        )
        approved = specialist.get("/specialist?tab=approved").text
        assert ready[0] in approved and ready[1] not in approved  # rejections are not listed
        rejected = specialist.get(f"/specialist/applications/{ready[1]}")
        assert rejected.status_code == 200 and "Rejected" in rejected.text
        assert (
            specialist.post(
                f"/specialist/applications/{ready[0]}/rerun", follow_redirects=False
            ).status_code
            == 409
        )

    def test_bulk_approve(self, specialist):
        ready = ids_in(specialist.get("/specialist?tab=ready").text, "/specialist/applications")
        resp = specialist.post(
            "/specialist/bulk-approve", data={"ids": ready}, follow_redirects=False
        )
        assert resp.status_code == 303
        assert (
            ids_in(specialist.get("/specialist?tab=ready").text, "/specialist/applications") == []
        )

    def test_rerun_records_a_new_run(self, specialist):
        app_id = ids_in(specialist.get("/specialist?tab=review").text, "/specialist/applications")[
            0
        ]
        assert (
            specialist.post(
                f"/specialist/applications/{app_id}/rerun", follow_redirects=False
            ).status_code
            == 303
        )
        assert "rerun" in specialist.get(f"/specialist/applications/{app_id}").text


class TestComments:
    def test_field_thread_round_trip(self, specialist, applicant):
        app_id = ids_in(specialist.get("/specialist?tab=review").text, "/specialist/applications")[
            0
        ]
        resp = specialist.post(
            f"/applications/{app_id}/comments",
            data={"field": "brand_name", "body": "Please confirm the spelling."},
            headers={"X-Partial": "1"},
        )
        assert resp.status_code == 200 and "Please confirm the spelling." in resp.text
        reply = applicant.post(
            f"/applications/{app_id}/comments",
            data={"field": "brand_name", "body": "Confirmed."},
            headers={"X-Partial": "1"},
        )
        assert reply.status_code == 200 and "Confirmed." in reply.text
        # Applicants never see resolve controls; the specialist's page carries them.
        review = specialist.get(f"/specialist/applications/{app_id}").text
        comment_id = re.search(r"/comments/([0-9a-f]{32})/resolve", review).group(1)
        assert (
            applicant.post(
                f"/applications/{app_id}/comments/{comment_id}/resolve", headers={"X-Partial": "1"}
            ).status_code
            == 403
        )
        resolved = specialist.post(
            f"/applications/{app_id}/comments/{comment_id}/resolve", headers={"X-Partial": "1"}
        )
        assert resolved.status_code == 200 and "Resolved" in resolved.text

    def test_new_activity_shows_a_badge_until_the_page_is_opened(self, specialist, applicant):
        app_id = ids_in(applicant.get("/applicant").text, "/applicant/applications")[0]
        applicant.get(f"/applicant/applications/{app_id}")  # seen; nothing new afterwards
        before = applicant.get("/me/unread").json()["count"]
        specialist.post(
            f"/applications/{app_id}/comments",
            data={"field": "brand_name", "body": "Is the apostrophe printed?"},
            headers={"X-Partial": "1"},
        )
        assert applicant.get("/me/unread").json()["count"] == before + 1
        inbox = applicant.get("/inbox").text
        assert "Is the apostrophe printed?" in inbox and "on <em>Brand Name</em>" in inbox
        assert "/inbox/open/comment/" in inbox
        assert 'data-unread-badge' in inbox and "hidden>" not in inbox.split("data-unread-badge")[1][:80]

        page = applicant.get(f"/applicant/applications/{app_id}").text
        assert "Is the apostrophe printed?" in page
        assert 'pill-xs">New</span>' in page and "has-new" in page
        # Opening the page consumed it: the badge drops and the marker is gone on reload.
        assert applicant.get("/me/unread").json()["count"] == before
        again = applicant.get(f"/applicant/applications/{app_id}").text
        assert 'pill-xs">New</span>' not in again and "has-new" not in again
        # The applicant's own reply is new for the specialist, not for the applicant.
        applicant.post(
            f"/applications/{app_id}/comments",
            data={"field": "brand_name", "body": "Yes, exactly as shown."},
            headers={"X-Partial": "1"},
        )
        assert applicant.get("/me/unread").json()["count"] == before
        review = specialist.get(f"/specialist/applications/{app_id}").text
        assert "Yes, exactly as shown." in review and 'pill-xs">New</span>' in review

    def test_clicking_one_inbox_item_keeps_the_others(self, specialist, applicant):
        app_id = ids_in(applicant.get("/applicant").text, "/applicant/applications")[0]
        applicant.get(f"/applicant/applications/{app_id}")
        before = applicant.get("/me/unread").json()["count"]  # other applications' items
        for n in range(3):
            specialist.post(
                f"/applications/{app_id}/comments",
                data={"field": "brand_name", "body": f"Question {n}"},
                headers={"X-Partial": "1"},
            )
        assert applicant.get("/me/unread").json()["count"] == before + 3
        inbox = applicant.get("/inbox").text
        links = re.findall(r"/inbox/open/comment/[0-9a-f]{32}", inbox)
        pills = inbox.count('pill-xs">New</span>')
        resp = applicant.get(links[0], follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/applicant/applications/{app_id}?via=inbox#field-brand_name"
        page = applicant.get(resp.headers["location"]).text
        assert page.count('pill-xs">New</span>') == 2  # the two unopened questions stay new
        assert applicant.get("/me/unread").json()["count"] == before + 2
        assert applicant.get("/inbox").text.count('pill-xs">New</span>') == pills - 1
        # A cross-tenant item id is a 404, not a leak.
        other = login(specialist.app, other_applicant(specialist.app))
        assert other.get(links[1], follow_redirects=False).status_code == 404

    def test_inbox_mark_all_read(self, specialist, applicant):
        app_id = ids_in(applicant.get("/applicant").text, "/applicant/applications")[0]
        applicant.get(f"/applicant/applications/{app_id}")
        specialist.post(
            f"/applications/{app_id}/comments",
            data={"field": "general", "body": "Quick question about the back panel."},
            headers={"X-Partial": "1"},
        )
        assert "Mark all as read" in applicant.get("/inbox").text
        resp = applicant.post("/inbox/read-all", follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"] == "/inbox"
        after = applicant.get("/inbox").text
        assert "Mark all as read" not in after and "Earlier" in after
        assert "Quick question about the back panel." in after  # kept, just no longer new

    def test_unread_endpoint_requires_login(self, anon):
        assert anon.get("/me/unread", headers={"Accept": "application/json"}).status_code == 401

    def test_empty_comment_and_unknown_field(self, specialist):
        app_id = ids_in(specialist.get("/specialist").text, "/specialist/applications")[0]
        assert (
            specialist.post(
                f"/applications/{app_id}/comments",
                data={"field": "brand_name", "body": "  "},
                headers={"X-Partial": "1"},
            ).status_code
            == 422
        )
        assert (
            specialist.post(
                f"/applications/{app_id}/comments",
                data={"field": "nope", "body": "x"},
                headers={"X-Partial": "1"},
            ).status_code
            == 422
        )

    def test_applicant_cannot_see_other_applicants_application(self, app, applicant):
        other = login(app, other_applicant(app))
        mine = ids_in(applicant.get("/applicant").text, "/applicant/applications")[0]
        assert other.get(f"/applicant/applications/{mine}").status_code == 404
        assert (
            other.post(
                f"/applications/{mine}/comments",
                data={"field": "general", "body": "hi"},
                headers={"X-Partial": "1"},
            ).status_code
            == 404
        )


class TestApplicantWorkflow:
    def test_dashboard_cards_filter_the_table(self, applicant):
        page = applicant.get("/applicant?filter=action").text
        assert 'href="/applicant?filter=action"' in page and "stat-active" in page
        assert "Showing <strong>Needs your action</strong>" in page
        assert "Correction requested" in page and "Under review" not in page
        assert "Under review" in applicant.get("/applicant?filter=review").text
        # An unknown filter falls back to the full list.
        everything = applicant.get("/applicant?filter=nope").text
        assert "Showing <strong>" not in everything and "Under review" in everything

    def test_dashboard(self, applicant):
        resp = applicant.get("/applicant")
        assert resp.status_code == 200 and "My applications" in resp.text

    def test_new_application_end_to_end(self, applicant, specialist):
        page = applicant.get("/applicant/applications/new")
        assert page.status_code == 200 and 'data-sample-id="old-tom-bourbon"' in page.text

        created = applicant.post(
            "/applicant/applications",
            data={"sample_id": "old-tom-title-case-warning", "beverage_type": "distilled_spirits"},
            headers={"Accept": "application/json"},
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["prefill"]["brand_name"] == "OLD TOM DISTILLERY"
        assert payload["prefill"]["net_contents"] == "750 mL"
        # Label-only items the applicant never types are reported so step 2 can show them.
        assert payload["label_read"]["qualifying_phrase"] == "Distilled and Bottled by"
        assert payload["label_read"]["health_warning"].lower().startswith("government warning")
        assert payload["label_read"]["sulfite_declaration"] is None
        app_id = payload["id"]

        check = applicant.post(
            f"/applicant/applications/{app_id}/precheck", data=FORM, headers={"X-Partial": "1"}
        )
        assert check.status_code == 200
        assert "needs corrections" in check.text and "capital letters" in check.text

        submitted = applicant.post(
            f"/applicant/applications/{app_id}/submit", data=FORM, follow_redirects=False
        )
        assert submitted.status_code == 303
        detail = applicant.get(f"/applicant/applications/{app_id}")
        assert detail.status_code == 200 and "In the review queue" in detail.text
        assert (
            applicant.post(
                f"/applicant/applications/{app_id}/precheck", data=FORM, headers={"X-Partial": "1"}
            ).status_code
            == 409
        )
        assert app_id in specialist.get("/specialist?tab=review").text

    def test_front_and_back_upload_creates_two_panels(self, applicant, specialist):
        created = applicant.post(
            "/applicant/applications",
            data={"beverage_type": "distilled_spirits"},
            files=[
                ("images", ("front.jpg", sample_bytes("old-tom-bourbon"), "image/jpeg")),
                ("images", ("back.jpg", sample_bytes("old-tom-title-case-warning"), "image/jpeg")),
            ],
            headers={"Accept": "application/json"},
        )
        assert created.status_code == 200
        payload = created.json()
        assert len(payload["image_urls"]) == 2
        assert payload["prefill"]["brand_name"] == "OLD TOM DISTILLERY"
        app_id = payload["id"]
        applicant.post(
            f"/applicant/applications/{app_id}/precheck", data=FORM, headers={"X-Partial": "1"}
        )
        applicant.post(
            f"/applicant/applications/{app_id}/submit", data=FORM, follow_redirects=False
        )
        page = specialist.get(f"/specialist/applications/{app_id}").text
        assert 'data-viewer-label="Front"' in page and 'data-viewer-label="Back"' in page

    def test_precheck_reuses_the_upload_read(self, applicant):
        created = applicant.post(
            "/applicant/applications",
            data={"sample_id": "old-tom-bourbon", "beverage_type": "distilled_spirits"},
        ).json()
        resp = applicant.post(
            f"/applicant/applications/{created['id']}/precheck",
            data=FORM,
            headers={"X-Partial": "1"},
        )
        assert resp.status_code == 200 and "reused from upload" in resp.text

    def test_unstated_type_is_taken_from_the_label(self, applicant, specialist):
        """Step 1 no longer asks for the type: the read decides it, step 2 shows the class's
        checklist, and the comparison runs under that class's rules."""
        page = applicant.get("/applicant/applications/new").text
        assert 'id="rules-checklist"' in page and "27 CFR" in page and "Detect from the label" in page
        created = applicant.post(
            "/applicant/applications", data={"sample_id": "stones-throw-wine"}
        ).json()
        assert created["prefill"]["beverage_type"] == "wine"
        form = {k: v for k, v in sample("stones-throw-wine")["application"].items() if v not in (None, False, "")}
        form.pop("beverage_type")  # leave it unstated on purpose
        resp = applicant.post(
            f"/applicant/applications/{created['id']}/precheck", data=form, headers={"X-Partial": "1"}
        )
        assert resp.status_code == 200
        assert "Wine · 27 CFR part 4" in resp.text and "type taken from the label" in resp.text
        assert "Sulfite Declaration" in resp.text and "27 CFR 4.32(e)" in resp.text
        assert "27 CFR 4.36" in resp.text  # the alcohol row cites the wine section
        submitted = applicant.post(
            f"/applicant/applications/{created['id']}/submit", data=form, follow_redirects=False
        )
        assert submitted.status_code == 303
        review = specialist.get(f"/specialist/applications/{created['id']}").text
        assert "Wine" in review and "taken from the label" in review

    def test_rules_reference_page_and_api(self, applicant, anon):
        page = applicant.get("/rules")
        assert page.status_code == 200 and "27 CFR part 7" in page.text and "Sulfite declaration" in page.text
        api = applicant.get("/api/rules").json()["rules"]
        assert [r["part"] for r in api] == [5, 4, 7]
        assert anon.get("/rules", follow_redirects=False).status_code == 303

    def test_wrong_product_type_is_caught(self, applicant):
        """A bourbon filed as wine: every text field can match and it is still wrong."""
        created = applicant.post(
            "/applicant/applications",
            data={"sample_id": "old-tom-bourbon", "beverage_type": "wine"},
        ).json()
        resp = applicant.post(
            f"/applicant/applications/{created['id']}/precheck",
            data={**FORM, "beverage_type": "wine"},
            headers={"X-Partial": "1"},
        )
        assert resp.status_code == 200
        assert "Type of Product" in resp.text and "filed as wine" in resp.text
        assert "needs corrections" in resp.text

    def test_submit_requires_brand_and_class(self, applicant):
        app_id = applicant.post(
            "/applicant/applications",
            data={"sample_id": "old-tom-bourbon"},
            headers={"Accept": "application/json"},
        ).json()["id"]
        resp = applicant.post(
            f"/applicant/applications/{app_id}/submit",
            data={"beverage_type": "wine"},
            headers={"X-Partial": "1"},
        )
        assert resp.status_code == 422

    def test_upload_of_own_image_in_demo_mode_still_creates_a_draft(self, applicant, label_png):
        created = applicant.post(
            "/applicant/applications",
            files={"images": ("mine.png", label_png, "image/png")},
            data={"beverage_type": "wine"},
            headers={"Accept": "application/json"},
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["prefill"] == {} and "ANTHROPIC_API_KEY" in payload["warning"]

    def test_a_failed_read_can_be_retried_on_the_stored_images(self, tmp_path, extraction, label_png):
        """A timeout at upload leaves the draft and its images in place; 'Read again' reads
        them once more without a second upload."""
        from labelverify.engine.extractors.base import ExtractionError

        class FlakyReader:
            name = "flaky"

            def __init__(self):
                self.calls = 0

            def extract(self, image, media_type):
                return self.extract_panels([(image, media_type)])

            def extract_panels(self, panels):
                self.calls += 1
                if self.calls == 1:
                    raise ExtractionError("The model did not respond within 20 s for 1 image.")
                return extraction.model_copy(deep=True)

        reader = FlakyReader()
        app = create_app(extractor=reader, database_url=f"sqlite:///{tmp_path}/r.db", secret="s")
        client = login(app, APPLICANT)
        created = client.post(
            "/applicant/applications",
            files={"images": ("mine.png", label_png, "image/png")},
            headers={"Accept": "application/json"},
        ).json()
        assert created["read_failed"] and created["prefill"] == {}
        assert "did not respond" in created["warning"]
        again = client.post(f"/applicant/applications/{created['id']}/read").json()
        assert not again["read_failed"] and again["prefill"]["brand_name"] == "OLD TOM DISTILLERY"
        assert again["prefill"]["beverage_type"] == "distilled_spirits"
        assert reader.calls == 2
        # The retried read is kept, so the pre-check needs no third call.
        resp = client.post(
            f"/applicant/applications/{created['id']}/precheck", data=FORM, headers={"X-Partial": "1"}
        )
        assert resp.status_code == 200 and "reused from upload" in resp.text and reader.calls == 2

    def test_garbage_upload_is_rejected(self, applicant):
        resp = applicant.post(
            "/applicant/applications",
            files={"images": ("x.png", b"nope", "image/png")},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 400 and "readable image" in resp.json()["detail"]

    def test_resubmit_after_correction(self, applicant, specialist):
        # Seeded data includes one application awaiting correction for Old Tom.
        dash = applicant.get("/applicant").text
        fix_id = re.search(
            r'class="attention-list".*?/applicant/applications/([0-9a-f]{32})', dash, re.S
        ).group(1)
        detail = applicant.get(f"/applicant/applications/{fix_id}")
        assert "Fix and resubmit" in detail.text and "Correction request" in detail.text
        resp = applicant.post(
            f"/applicant/applications/{fix_id}/resubmit",
            data={**FORM, "message": "Uploaded corrected artwork."},
            files=[("images", ("fixed.png", sample_bytes("old-tom-bourbon"), "image/png"))],
            follow_redirects=False,
        )
        assert resp.status_code == 303
        after = applicant.get(f"/applicant/applications/{fix_id}").text
        assert "Resubmitted" in after and "Uploaded corrected artwork." in after and "v2" in after
        assert fix_id in specialist.get("/specialist?tab=ready").text

    def test_label_image_is_served_to_owner_and_specialist(self, applicant, specialist):
        detail = applicant.get("/applicant").text
        app_id = ids_in(detail, "/applicant/applications")[0]
        page = applicant.get(f"/applicant/applications/{app_id}").text
        image_url = re.search(rf"/applications/{app_id}/images/[0-9a-f]{{32}}", page).group(0)
        assert applicant.get(image_url).headers["content-type"] == "image/jpeg"
        assert specialist.get(image_url).status_code == 200


class TestBatch:
    def _zip(self, names):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for n in names:
                zf.writestr(n, sample_bytes(n.rsplit(".", 1)[0]))
        return buf.getvalue()

    def test_batch_page_and_template(self, applicant):
        assert applicant.get("/applicant/batches").status_code == 200
        csv = applicant.get("/applicant/batches/template.csv")
        assert csv.status_code == 200 and csv.text.startswith("image,beverage_type")

    def test_batch_upload_processes_rows(self, applicant, specialist):
        rows = [
            "image,beverage_type,brand_name,class_type,alcohol_content,net_contents,producer_name,producer_address,is_import,country_of_origin"
        ]
        for sid in ("old-tom-bourbon", "old-tom-abv-mismatch"):
            a = sample(sid)["application"]
            fname = sample(sid)["file"]
            rows.append(
                f'{fname},{a["beverage_type"]},{a["brand_name"]},{a["class_type"]},{a["alcohol_content"]},{a["net_contents"]},{a["producer_name"]},"{a["producer_address"]}",false,'
            )
        rows.append("ghost.png,wine,Ghost,Red,,,,,false,")
        csv = "\n".join(rows).encode()
        files = [sample(s)["file"] for s in ("old-tom-bourbon", "old-tom-abv-mismatch")]
        resp = applicant.post(
            "/applicant/batches",
            files={
                "csv_file": ("peak.csv", csv, "text/csv"),
                "zip_file": ("labels.zip", self._zip(files), "application/zip"),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        batch_url = resp.headers["location"]
        rows_resp = applicant.get(batch_url + "/rows")
        assert rows_resp.headers["X-Batch-Status"] == "done"
        assert (
            "1 all fields match" in rows_resp.text
            and "1 corrections needed" in rows_resp.text
            and "1 could not be checked" in rows_resp.text
        )
        assert applicant.get(batch_url).status_code == 200
        assert "peak.csv" in applicant.get("/applicant/batches").text
        assert "OLD TOM DISTILLERY" in specialist.get("/specialist").text  # the batch rows are queued

    def test_sample_batch_downloads_and_runs_end_to_end(self, applicant, specialist):
        """The two downloads on the batch page are enough to exercise the whole flow."""
        csv_resp = applicant.get("/applicant/batches/sample.csv")
        zip_resp = applicant.get("/applicant/batches/sample-images.zip")
        assert csv_resp.status_code == 200 and len(csv_resp.text.strip().splitlines()) == 16  # header + 15 rows
        assert zip_resp.status_code == 200 and zip_resp.headers["content-type"] == "application/zip"
        names = zipfile.ZipFile(io.BytesIO(zip_resp.content)).namelist()
        assert len(names) == 14 and "not-a-label.jpg" in names and "missing-photo.jpg" not in names

        page = applicant.get("/applicant/batches").text
        assert "/applicant/batches/sample.csv" in page and "sample-images.zip" in page

        resp = applicant.post(
            "/applicant/batches",
            files={
                "csv_file": ("sample.csv", csv_resp.content, "text/csv"),
                "zip_file": ("images.zip", zip_resp.content, "application/zip"),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        rows = applicant.get(resp.headers["location"] + "/rows")
        assert rows.headers["X-Batch-Status"] == "done"
        assert "15 of 15 checked" in rows.text
        assert ",," in csv_resp.text  # two rows leave the type blank on purpose
        assert "3 all fields match" in rows.text and "3 need a look" in rows.text
        assert "7 corrections needed" in rows.text and "2 could not be checked" in rows.text
        assert "missing-photo.jpg" in rows.text and "Not checked" in rows.text
        assert "was not found in the zip" in rows.text  # the missing image, explained
        assert "Demo mode can only read" in rows.text  # the non-label photo, explained (demo reader)
        assert "Sunset Ridge" in specialist.get("/specialist").text

    def test_batch_validation_errors_render(self, applicant):
        resp = applicant.post(
            "/applicant/batches",
            files={
                "csv_file": ("bad.csv", b"brand_name\nX", "text/csv"),
                "zip_file": ("z.zip", b"nope", "application/zip"),
            },
        )
        assert resp.status_code == 422 and "missing required column" in resp.text


class TestApi:
    def test_verify_with_sample(self, anon):
        resp = anon.post(
            "/api/verify",
            data={
                "sample_id": "old-tom-bourbon",
                "application": json.dumps(sample("old-tom-bourbon")["application"]),
            },
        )
        assert resp.status_code == 200 and resp.json()["recommendation"] == "approve"

    @pytest.mark.parametrize("entry", load_manifest(), ids=lambda s: s["id"])
    def test_every_sample_gives_its_promised_recommendation(self, anon, entry):
        resp = anon.post(
            "/api/verify",
            data={"sample_id": entry["id"], "application": json.dumps(entry["application"])},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["recommendation"] == entry["expected"]

    def test_verify_with_fixture_extractor_and_upload(self, tmp_path, extraction, label_png):
        app = create_app(
            extractor=FixtureExtractor(extraction),
            database_url=f"sqlite:///{tmp_path}/f.db",
            seed_data=False,
        )
        client = TestClient(app)
        resp = client.post(
            "/api/verify",
            data={"application": json.dumps(FORM)},
            files={"image": ("l.png", label_png, "image/png")},
        )
        assert resp.status_code == 200 and resp.json()["source"] == "upload:l.png"

    def test_api_errors(self, anon, label_png):
        assert anon.post("/api/verify", data={"application": "{}"}).status_code == 400
        assert (
            anon.post(
                "/api/verify", data={"sample_id": "old-tom-bourbon", "application": "{}"}
            ).status_code
            == 422
        )
        assert (
            anon.post(
                "/api/verify",
                data={"application": json.dumps(FORM)},
                files={"image": ("l.png", label_png, "image/png")},
            ).status_code
            == 502
        )
        assert anon.get("/api/samples").status_code == 200
        assert (
            anon.get("/api/samples/old-tom-bourbon/image")
            .headers["content-type"]
            .startswith("image/")
        )
