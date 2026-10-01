"""Two-party communication scenarios, run from the CLI:

    .venv/bin/python -m pytest tests/test_communication.py -v

Each test is one situation between an applicant and a labeling specialist: who sends,
who receives, what the receiver sees where, when it counts as read, and what the other
side must never see. They run against a clean database with only the demo users (no
seeded applications), so every count starts at zero and the numbers in the assertions
are exactly the messages the scenario sent.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from labelverify.engine.extractors import DemoExtractor
from labelverify.engine.extractors.demo import load_manifest
from labelverify.web.app import create_app
from labelverify.web.seed import DEMO_PASSWORD, ensure_user, seed_users

SARAH = "sarah.chen@ttb.gov"  # specialist
MARIA = "maria@alvarezlabels.com"  # applicant, Alvarez Label Services
# Extra accounts the scenarios create on demand; the demo itself seeds only the two above.
JENNY = ("jenny.park@ttb.gov", "Jenny Park", "specialist", "TTB Label Compliance")
DEVIN = ("compliance@stonesthrow.wine", "Devin Okafor", "applicant", "Stone's Throw Vineyards")

NEW_PILL = 'pill-xs">New</span>'


def text_of(html: str) -> str:
    """Strip tags, collapse whitespace, undo escaping: assertions then read like the screen."""
    import html as html_lib

    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))


@pytest.fixture
def app(tmp_path):
    """The web app on a fresh database with demo users only."""
    application = create_app(
        extractor=DemoExtractor(),
        database_url=f"sqlite:///{tmp_path}/comms.db",
        secret="comms-secret",
        seed_data=False,
    )
    with application.state.session_factory() as db:
        seed_users(db)
        db.commit()
    return application


class Party:
    """One logged-in person, with the handful of actions the scenarios need."""

    def __init__(self, app, email: str | tuple):
        if isinstance(email, tuple):  # an extra account: create it first
            spec = dict(zip(("email", "name", "role", "organization"), email, strict=True))
            with app.state.session_factory() as db:
                ensure_user(db, **spec)
                db.commit()
            email = spec["email"]
        self.app = app
        self.email = email
        self.role = "specialist" if email.endswith("@ttb.gov") else "applicant"
        self.client = TestClient(app)
        resp = self.client.post(
            "/login", data={"email": email, "password": DEMO_PASSWORD}, follow_redirects=False
        )
        assert resp.status_code == 303, f"login failed for {email}"

    # ----- reading
    def unread(self) -> int:
        """What the sidebar badge polls: unread inbox items, computed live."""
        return self.client.get("/me/unread").json()["count"]

    def inbox(self) -> str:
        return self.client.get("/inbox").text

    def inbox_links(self, kind: str | None = None) -> list[str]:
        pattern = rf"/inbox/open/{kind or '[a-z]+'}/[0-9a-f]{{32}}"
        return list(dict.fromkeys(re.findall(pattern, self.inbox())))

    def inbox_new(self) -> int:
        return self.inbox().count(NEW_PILL)

    def detail_url(self, app_id: str) -> str:
        return f"/{self.role}/applications/{app_id}"

    def open_app(self, app_id: str) -> str:
        """Open the application from a list: reads everything on it."""
        resp = self.client.get(self.detail_url(app_id))
        assert resp.status_code == 200, resp.text[:200]
        return resp.text

    def open_item(self, link: str) -> tuple[str, str]:
        """Click an inbox item: reads that item only. Returns (landing url, page html)."""
        resp = self.client.get(link, follow_redirects=False)
        assert resp.status_code == 303, resp.text[:200]
        location = resp.headers["location"]
        return location, self.client.get(location).text

    # ----- writing
    def comment(self, app_id: str, field: str, body: str):
        return self.client.post(
            f"/applications/{app_id}/comments",
            data={"field": field, "body": body},
            headers={"X-Partial": "1"},
        )

    def resolve(self, app_id: str, comment_id: str, resolved: bool = True):
        return self.client.post(
            f"/applications/{app_id}/comments/{comment_id}/resolve",
            data={"resolved": "true" if resolved else "false"},
            headers={"X-Partial": "1"},
        )

    def mark_all_read(self):
        return self.client.post("/inbox/read-all", follow_redirects=False)

    # ----- applicant workflow
    def submit_new(self, sample_id: str) -> str:
        """Create an application from a bundled sample label and submit it."""
        created = self.client.post(
            "/applicant/applications",
            data={"sample_id": sample_id, "beverage_type": _form(sample_id)["beverage_type"]},
        )
        assert created.status_code == 200, created.text
        app_id = created.json()["id"]
        submitted = self.client.post(
            f"/applicant/applications/{app_id}/submit", data=_form(sample_id), follow_redirects=False
        )
        assert submitted.status_code == 303, submitted.text
        return app_id

    def resubmit(self, app_id: str, sample_id: str, message: str = ""):
        return self.client.post(
            f"/applicant/applications/{app_id}/resubmit",
            data={**_form(sample_id), "message": message},
            follow_redirects=False,
        )

    # ----- specialist workflow
    def decide(self, app_id: str, action: str, notice_body: str = ""):
        return self.client.post(
            f"/specialist/applications/{app_id}/decision",
            data={"action": action, "notice_body": notice_body, "notice_source": "template"},
            follow_redirects=False,
        )

    def draft_notice(self, app_id: str) -> str:
        resp = self.client.post(
            f"/specialist/applications/{app_id}/notice", headers={"X-Partial": "1"}
        )
        assert resp.status_code == 200, resp.text
        return re.search(r"<textarea[^>]*>(.*?)</textarea>", resp.text, re.S).group(1)

    def request_correction(self, app_id: str):
        return self.decide(app_id, "request_correction", self.draft_notice(app_id))


def _form(sample_id: str) -> dict[str, str]:
    data = next(s for s in load_manifest() if s["id"] == sample_id)["application"]
    return {k: str(v) for k, v in data.items() if v not in (None, "", False)}


def comment_ids(html: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"/comments/([0-9a-f]{32})/resolve", html)))


@pytest.fixture
def sarah(app):
    return Party(app, SARAH)


@pytest.fixture
def maria(app):
    return Party(app, MARIA)


# =============================================================================== scenarios


class TestSendAndReceive:
    def test_a_submission_is_queue_work_not_a_message(self, sarah, maria):
        """Applicant submits. The specialist sees it in the queue, but the inbox stays quiet:
        nobody wrote to anyone yet."""
        app_id = maria.submit_new("old-tom-bourbon")
        assert app_id in sarah.client.get("/specialist").text
        assert sarah.unread() == 0 and maria.unread() == 0
        assert "caught up" in sarah.inbox() and "caught up" in maria.inbox()

    def test_specialist_question_reaches_the_applicant(self, sarah, maria):
        """Specialist asks on a field. The applicant's badge, inbox, and page all show it,
        anchored to that field, with the thread already open."""
        app_id = maria.submit_new("old-tom-bourbon")
        sarah.open_app(app_id)
        assert maria.unread() == 0

        sarah.comment(app_id, "brand_name", "Is the apostrophe printed on the label?")

        assert maria.unread() == 1
        inbox = text_of(maria.inbox())
        assert "Sarah Chen commented on Brand Name" in inbox
        assert "Is the apostrophe printed on the label?" in inbox
        assert sarah.unread() == 0  # your own message is never news to you

        page = maria.open_app(app_id)
        assert page.count(NEW_PILL) == 1 and "has-new" in page
        assert 'id="thread-brand_name" hidden' not in page  # thread auto-opens
        assert maria.unread() == 0

    def test_applicant_reply_reaches_the_specialist(self, sarah, maria):
        """The reverse direction: the applicant answers on the same field, the specialist is
        told, and the reply shows under the question."""
        app_id = maria.submit_new("old-tom-bourbon")
        sarah.comment(app_id, "brand_name", "Is the apostrophe printed?")
        maria.open_app(app_id)
        maria.comment(app_id, "brand_name", "Yes, exactly as on the artwork.")

        assert sarah.unread() == 1 and maria.unread() == 0
        inbox = text_of(sarah.inbox())
        assert "Maria Alvarez replied on Brand Name" in inbox
        page = sarah.open_app(app_id)
        assert page.index("Is the apostrophe printed?") < page.index("Yes, exactly as on the artwork.")
        assert page.count(NEW_PILL) == 1

    def test_general_discussion_both_directions(self, sarah, maria):
        """Messages not tied to a field travel the same way and land on the discussion card."""
        app_id = maria.submit_new("old-tom-bourbon")
        sarah.open_app(app_id)
        maria.comment(app_id, "general", "We can send the back label too if that helps.")
        sarah.comment(app_id, "general", "Please do.")

        assert sarah.unread() == 1 and maria.unread() == 1
        assert "Sarah Chen commented in the general discussion" in text_of(maria.inbox())
        location, page = maria.open_item(maria.inbox_links("comment")[0])
        assert location.endswith("#thread-host-general")
        assert "Please do." in page and maria.unread() == 0

    def test_every_message_counts_once_and_in_order(self, sarah, maria):
        """Five messages on three fields produce five inbox items, newest first, and a
        per-field count on the page."""
        app_id = maria.submit_new("old-tom-bourbon")
        maria.open_app(app_id)
        for n, field in enumerate(["brand_name", "brand_name", "net_contents", "general", "general"]):
            sarah.comment(app_id, field, f"Message {n}")

        assert maria.unread() == 5
        inbox = maria.inbox()
        assert [int(m) for m in re.findall(r"Message (\d)", inbox)] == [4, 3, 2, 1, 0]
        page = maria.open_app(app_id)
        assert "2 new comments on this field" in page  # brand name toggle tooltip
        assert '2 new</span>' in page  # general discussion title
        assert maria.unread() == 0


class TestNoticesAndDecisions:
    def test_correction_request_delivers_notice_and_field_threads(self, sarah, maria):
        """A correction request is a notice plus one comment per flagged field. The
        applicant gets each as its own inbox item; the notice link lands on the notice."""
        app_id = maria.submit_new("old-tom-title-case-warning")
        maria.open_app(app_id)
        sarah.open_app(app_id)
        assert sarah.request_correction(app_id).status_code == 303

        assert maria.unread() == 2  # the notice and the health-warning thread
        inbox = text_of(maria.inbox())
        assert "Sarah Chen sent a correction request" in inbox
        assert "Sarah Chen commented on Government Health Warning" in inbox
        location, page = maria.open_item(maria.inbox_links("notice")[0])
        assert location.endswith("#notice") and "Correction request" in page
        assert page.count(NEW_PILL) == 1  # the field thread is still new
        assert maria.unread() == 1
        assert sarah.unread() == 0

    def test_resubmission_tells_the_specialist_with_the_message(self, sarah, maria):
        """The applicant fixes the label and resubmits with a note: the specialist gets the
        resubmission and the note as two items, and the case is back in the queue."""
        app_id = maria.submit_new("old-tom-title-case-warning")
        sarah.request_correction(app_id)
        maria.open_app(app_id)
        resp = maria.resubmit(app_id, "old-tom-bourbon", message="Heading reset in capitals.")
        assert resp.status_code == 303

        assert sarah.unread() == 2
        inbox = text_of(sarah.inbox())
        assert "Maria Alvarez resubmitted the label" in inbox
        assert "Heading reset in capitals." in inbox
        assert app_id in sarah.client.get("/specialist?tab=open").text
        location, page = sarah.open_item(sarah.inbox_links("status")[0])
        assert location.endswith("#history") and "Resubmitted" in page
        assert sarah.unread() == 1

    def test_approval_and_rejection_notify_the_applicant_and_close_the_threads(self, app, sarah, maria):
        """A decision reaches the applicant as one item; after it, neither side can post."""
        approved = maria.submit_new("old-tom-bourbon")
        rejected = maria.submit_new("old-tom-abv-mismatch")
        for app_id in (approved, rejected):
            maria.open_app(app_id)
        assert sarah.decide(approved, "approve").status_code == 303
        assert sarah.decide(rejected, "reject", "The alcohol content does not match.").status_code == 303

        assert maria.unread() == 2
        inbox = text_of(maria.inbox())
        assert "Sarah Chen approved the label" in inbox
        assert "Sarah Chen sent a rejection notice" in inbox
        assert "The alcohol content does not match." in inbox

        assert maria.comment(approved, "general", "Thanks!").status_code == 422
        assert sarah.comment(rejected, "general", "Follow-up").status_code == 422
        assert "comment-form" not in maria.open_app(approved)


class TestReadState:
    def test_inbox_click_reads_one_item_direct_open_reads_all(self, sarah, maria):
        """Three questions. Clicking one in the inbox leaves two. Opening the application from
        the dashboard afterwards clears the rest."""
        app_id = maria.submit_new("old-tom-bourbon")
        maria.open_app(app_id)
        for n in range(3):
            sarah.comment(app_id, "brand_name", f"Question {n}")
        assert maria.unread() == 3 and maria.inbox_new() == 3

        location, page = maria.open_item(maria.inbox_links("comment")[0])
        assert location == f"/applicant/applications/{app_id}?via=inbox#field-brand_name"
        assert page.count(NEW_PILL) == 2
        assert maria.unread() == 2 and maria.inbox_new() == 2
        assert "Earlier" in maria.inbox()  # the opened one is kept, just not new

        maria.open_app(app_id)
        assert maria.unread() == 0 and maria.inbox_new() == 0

    def test_mark_all_read_clears_everything_but_keeps_history(self, sarah, maria):
        a, b = maria.submit_new("old-tom-bourbon"), maria.submit_new("old-tom-abv-mismatch")
        sarah.comment(a, "general", "One")
        sarah.comment(b, "general", "Two")
        assert maria.unread() == 2
        assert maria.mark_all_read().status_code == 303
        assert maria.unread() == 0
        inbox = maria.inbox()
        assert "One" in inbox and "Two" in inbox and NEW_PILL not in inbox

    def test_reading_is_per_person_even_for_two_specialists(self, app, sarah, maria):
        """Sarah reading a reply does not read it for Jenny."""
        jenny = Party(app, JENNY)
        app_id = maria.submit_new("old-tom-bourbon")
        maria.comment(app_id, "general", "Is a neck label required?")
        assert sarah.unread() == 1 and jenny.unread() == 1
        sarah.open_app(app_id)
        assert sarah.unread() == 0 and jenny.unread() == 1
        jenny.open_item(jenny.inbox_links("comment")[0])
        assert jenny.unread() == 0

    def test_later_messages_are_new_again(self, sarah, maria):
        """Reading is a moment in time: anything after it is unread again."""
        app_id = maria.submit_new("old-tom-bourbon")
        sarah.comment(app_id, "general", "First")
        maria.open_app(app_id)
        assert maria.unread() == 0
        sarah.comment(app_id, "general", "Second")
        assert maria.unread() == 1
        assert maria.open_app(app_id).count(NEW_PILL) == 1  # only "Second" is marked


class TestResolutionAndIsolation:
    def test_resolving_a_thread_is_visible_to_the_applicant_and_only_the_specialist_can(self, sarah, maria):
        app_id = maria.submit_new("old-tom-bourbon")
        sarah.comment(app_id, "brand_name", "Please confirm the spelling.")
        maria.comment(app_id, "brand_name", "Confirmed.")
        cid = comment_ids(sarah.open_app(app_id))[0]
        assert maria.resolve(app_id, cid).status_code == 403
        assert "Resolved" in sarah.resolve(app_id, cid).text
        assert "Resolved" in maria.open_app(app_id)
        assert "Resolved" not in sarah.resolve(app_id, cid, resolved=False).text

    def test_applicants_never_see_each_others_conversations(self, app, sarah, maria):
        """Another applicant cannot read, reply to, or open inbox items on Maria's case."""
        devin = Party(app, DEVIN)
        app_id = maria.submit_new("old-tom-bourbon")
        sarah.comment(app_id, "general", "Question for Old Tom only.")
        assert devin.unread() == 0 and "Old Tom" not in devin.inbox()
        assert devin.client.get(f"/applicant/applications/{app_id}").status_code == 404
        assert devin.comment(app_id, "general", "Hello?").status_code == 404
        link = maria.inbox_links("comment")[0]
        assert devin.client.get(link, follow_redirects=False).status_code == 404
        assert maria.unread() == 1  # the failed attempts changed nothing

    def test_specialists_see_every_applicants_messages(self, app, sarah, maria):
        devin = Party(app, DEVIN)
        a = maria.submit_new("old-tom-bourbon")
        b = devin.submit_new("stones-throw-wine")
        maria.comment(a, "general", "From Old Tom")
        devin.comment(b, "general", "From Stone's Throw")
        assert sarah.unread() == 2
        inbox = text_of(sarah.inbox())
        assert "Alvarez Label Services" in inbox and "Stone's Throw Vineyards" in inbox


class TestLiveness:
    def test_the_badge_endpoint_reflects_every_change_immediately(self, sarah, maria):
        """What the sidebar polls every 30 seconds is computed on each call, so there is no
        cache to go stale: each action moves the number at once."""
        app_id = maria.submit_new("old-tom-bourbon")
        expected = 0
        for step in range(4):
            sarah.comment(app_id, "general", f"Update {step}")
            expected += 1
            assert maria.unread() == expected
        maria.open_item(maria.inbox_links("comment")[0])
        assert maria.unread() == expected - 1
        maria.open_app(app_id)
        assert maria.unread() == 0

    def test_conversation_and_read_state_survive_a_restart(self, tmp_path):
        """Stop the app and start it again on the same database: nothing is lost."""
        url = f"sqlite:///{tmp_path}/restart.db"
        first = create_app(extractor=DemoExtractor(), database_url=url, secret="s", seed_data=False)
        with first.state.session_factory() as db:
            seed_users(db)
            db.commit()
        sarah, maria = Party(first, SARAH), Party(first, MARIA)
        app_id = maria.submit_new("old-tom-bourbon")
        sarah.comment(app_id, "brand_name", "Before the restart")
        sarah.comment(app_id, "general", "Also before")
        maria.open_item(maria.inbox_links("comment")[0])
        assert maria.unread() == 1

        second = create_app(extractor=DemoExtractor(), database_url=url, secret="s", seed_data=False)
        maria2 = Party(second, MARIA)
        assert maria2.unread() == 1
        page = maria2.open_app(app_id)
        assert "Before the restart" in page and "Also before" in page
        assert maria2.unread() == 0
