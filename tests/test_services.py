"""Workflow tests against a throwaway SQLite database, no HTTP involved."""

from __future__ import annotations

import io
import zipfile

import pytest

from labelverify.engine.extractors import DemoExtractor, FixtureExtractor
from labelverify.engine.extractors.demo import SAMPLES_DIR, load_manifest
from labelverify.engine.models import ApplicationData, LabelExtraction
from labelverify.engine.preprocess import UnreadableImageError
from labelverify.web import services
from labelverify.web.auth import verify_password
from labelverify.web.db import init_db, make_engine, make_session_factory
from labelverify.web.models import ApplicationStatus, Role
from labelverify.web.seed import DEMO_PASSWORD, seed, seed_users


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def users(db):
    users = seed_users(db)
    db.commit()
    return users


def sample(sample_id: str) -> dict:
    return next(s for s in load_manifest() if s["id"] == sample_id)


def sample_bytes(sample_id: str) -> bytes:
    return (SAMPLES_DIR / sample(sample_id)["file"]).read_bytes()


def make_app(db, users, sample_id="old-tom-bourbon", extractor=None):
    applicant = users["maria@alvarezlabels.com"]
    data = ApplicationData.model_validate(sample(sample_id)["application"])
    app = services.create_draft(db, applicant, data, [(sample_bytes(sample_id), "label.png")])
    services.record_run(db, app, extractor or DemoExtractor(), "precheck")
    db.commit()
    return app, applicant


class TestUsersAndSerials:
    def test_seed_users_have_roles_and_passwords(self, users):
        assert users["sarah.chen@ttb.gov"].role == Role.SPECIALIST
        assert users["maria@alvarezlabels.com"].role == Role.APPLICANT
        assert verify_password(DEMO_PASSWORD, users["sarah.chen@ttb.gov"].password_hash)
        assert not verify_password("wrong", users["sarah.chen@ttb.gov"].password_hash)

    def test_serials_are_unique_and_readable(self, db, users):
        import re

        app1, _ = make_app(db, users)
        app2, _ = make_app(db, users)
        assert re.fullmatch(r"COLA-\d{4}-[A-Z2-9]{6}", app1.serial)
        assert app1.serial != app2.serial


class TestDraftAndPrecheck:
    def test_draft_has_prepared_image_and_run(self, db, users):
        app, _ = make_app(db, users)
        assert app.status == ApplicationStatus.DRAFT
        assert app.current_image.media_type == "image/jpeg"
        assert app.latest_run.trigger == "precheck"
        assert app.recommendation == "approve"
        assert app.risk_score == 0

    def test_mismatch_sample_scores_risk(self, db, users):
        app, _ = make_app(db, users, "old-tom-abv-mismatch")
        assert app.recommendation == "request_correction"
        assert app.risk_score >= 10

    def test_extraction_failure_is_recorded_not_raised(self, db, users):
        applicant = users["maria@alvarezlabels.com"]
        data = ApplicationData.model_validate(sample("old-tom-bourbon")["application"])
        # A valid image that the demo extractor does not recognize:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (400, 400), "white").save(buf, format="PNG")
        app = services.create_draft(db, applicant, data, [(buf.getvalue(), "blank.png")])
        run = services.record_run(db, app, DemoExtractor(), "precheck")
        assert run.recommendation == "error"
        assert "ANTHROPIC_API_KEY" in run.error
        assert app.latest_run_id == app.latest_run.id and app.recommendation == "error"

    def test_the_upload_read_is_reused_until_the_images_change(self, db, users, extraction):
        """One model call per upload: the pre-check and the submission compare against the
        read made at upload time. A fresh read is only made on request or after a new
        label set is attached."""
        reader = FixtureExtractor(extraction)
        reader.name = "fixture"
        applicant = users["maria@alvarezlabels.com"]
        data = ApplicationData.model_validate(sample("old-tom-bourbon")["application"])
        app = services.create_draft(
            db, applicant, data, [(sample_bytes("old-tom-bourbon"), "a.png")]
        )
        read, _, _ = services.extract_for_prefill(reader, app.current_images)
        services.remember_extraction(app, read, reader.name, 4321)
        db.commit()
        assert len(reader.calls) == 1

        run = services.record_run(db, app, reader, "precheck")
        result = services.result_of(run)
        assert len(reader.calls) == 1 and result.reused_read and result.extraction_ms == 4321
        services.record_run(db, app, reader, "submit")
        assert len(reader.calls) == 1

        fresh = services.record_run(db, app, reader, "rerun", fresh=True)
        assert len(reader.calls) == 2 and not services.result_of(fresh).reused_read
        assert app.extraction_version == app.current_version  # the fresh read is kept too

        services.attach_images(db, app, [(sample_bytes("old-tom-abv-mismatch"), "b.png")])
        services.record_run(db, app, reader, "resubmit")
        assert len(reader.calls) == 3  # new images, new read

    def test_prefill_fields_come_from_extraction(self):
        extraction = DemoExtractor().extract(sample_bytes("old-tom-bourbon"), "image/png")
        fields = services.prefill_fields(extraction)
        assert fields["brand_name"] == "OLD TOM DISTILLERY"
        assert fields["net_contents"] == "750 mL"
        assert fields["beverage_type"] == "distilled_spirits"  # read off the label

    def test_an_unstated_type_is_inferred_and_kept_on_the_application(self, db, users):
        applicant = users["maria@alvarezlabels.com"]
        data = ApplicationData.model_validate(
            {**sample("harbor-light-ipa-net-contents")["application"], "beverage_type": None}
        )
        app = services.create_draft(
            db, applicant, data, [(sample_bytes("harbor-light-ipa-net-contents"), "can.png")]
        )
        assert app.beverage_type == "" and not app.beverage_type_inferred
        run = services.record_run(db, app, DemoExtractor(), "precheck")
        result = services.result_of(run)
        assert result.beverage_type == "malt_beverage" and result.beverage_type_inferred
        assert app.beverage_type == "malt_beverage" and app.beverage_type_inferred
        # The applicant later states it explicitly: the inference flag clears.
        services.update_fields(
            db and app,
            ApplicationData.model_validate(sample("harbor-light-ipa-net-contents")["application"]),
        )
        assert not app.beverage_type_inferred


class TestLifecycle:
    def test_submit_review_approve(self, db, users):
        app, applicant = make_app(db, users)
        sarah = users["sarah.chen@ttb.gov"]
        services.submit(db, app, applicant)
        assert app.status == ApplicationStatus.SUBMITTED and app.submitted_at is not None
        assert services.claim_for_review(db, app, sarah) is True
        assert app.status == ApplicationStatus.UNDER_REVIEW and app.specialist_id == sarah.id
        services.decide(db, app, sarah, "approve")
        assert app.status == ApplicationStatus.APPROVED and app.decided_at is not None
        assert [e.to_status for e in app.events] == [
            "draft",
            "submitted",
            "under_review",
            "approved",
        ]

    def test_request_correction_creates_notice_and_field_comments(self, db, users):
        app, applicant = make_app(db, users, "old-tom-title-case-warning")
        sarah = users["sarah.chen@ttb.gov"]
        services.submit(db, app, applicant)
        draft = services.draft_correction(app, use_ai=False)
        assert draft.source == "template"
        assert "GOVERNMENT WARNING" in draft.body and app.serial in draft.body
        services.decide(
            db, app, sarah, "request_correction", notice_body=draft.body, notice_source=draft.source
        )
        assert app.status == ApplicationStatus.CORRECTION_REQUESTED
        assert len(app.notices) == 1
        grouped = services.comments_by_field(app)
        assert "health_warning" in grouped and grouped["health_warning"][0].author_id == sarah.id

    def test_correction_requires_notice(self, db, users):
        app, applicant = make_app(db, users, "old-tom-title-case-warning")
        services.submit(db, app, applicant)
        with pytest.raises(services.WorkflowError, match="notice"):
            services.decide(
                db, app, users["sarah.chen@ttb.gov"], "request_correction", notice_body=""
            )

    def test_resubmit_reruns_and_returns_to_queue(self, db, users):
        app, applicant = make_app(db, users, "old-tom-abv-mismatch")
        sarah = users["sarah.chen@ttb.gov"]
        services.submit(db, app, applicant)
        draft = services.draft_correction(app, use_ai=False)
        services.decide(db, app, sarah, "request_correction", notice_body=draft.body)
        # Applicant fixes the application value and uploads the corrected label.
        fixed = ApplicationData.model_validate(sample("old-tom-bourbon")["application"])
        run = services.resubmit(
            db,
            app,
            applicant,
            fixed,
            DemoExtractor(),
            uploads=[(sample_bytes("old-tom-bourbon"), "fixed.png")],
            message="Corrected the proof.",
        )
        assert run.trigger == "resubmit" and run.recommendation == "approve"
        assert app.status == ApplicationStatus.RESUBMITTED
        assert len(app.images) == 2 and app.current_image.filename == "fixed.png"
        assert app.recommendation == "approve"
        assert any(c.field == "general" and "Corrected" in c.body for c in app.comments)

    def test_illegal_transition_is_rejected(self, db, users):
        app, applicant = make_app(db, users)
        with pytest.raises(services.WorkflowError):
            services.decide(db, app, users["sarah.chen@ttb.gov"], "approve")  # still a draft
        with pytest.raises(services.WorkflowError):
            services.resubmit(
                db,
                app,
                applicant,
                ApplicationData.model_validate(sample("old-tom-bourbon")["application"]),
                DemoExtractor(),
            )

    def test_comments_and_resolution(self, db, users):
        app, applicant = make_app(db, users)
        sarah = users["sarah.chen@ttb.gov"]
        c = services.add_comment(db, app, sarah, "brand_name", "Please confirm the apostrophe.")
        services.add_comment(db, app, applicant, "brand_name", "Confirmed, it is printed as shown.")
        services.resolve_comment(db, c)
        assert c.resolved is True
        with pytest.raises(services.WorkflowError):
            services.add_comment(db, app, sarah, "nope", "x")


class TestUnreadActivity:
    def test_other_partys_comments_are_unread_until_the_page_is_opened(self, db, users):
        app, applicant = make_app(db, users)
        sarah = users["sarah.chen@ttb.gov"]
        services.submit(db, app, applicant)
        services.add_comment(db, app, sarah, "brand_name", "Please confirm the apostrophe.")
        services.add_comment(db, app, sarah, "general", "Also, which panel is the front?")
        services.add_comment(db, app, applicant, "general", "The left one.")
        db.commit()

        # The applicant has never opened it: both of Sarah's comments are new, their own is not.
        unread = services.unread_for(db, app, applicant, consume=False)
        assert unread.count == 2 and unread.fields == {"brand_name": 1, "general": 1}
        assert services.unread_counts(db, applicant, [app.id]) == {app.id: 2}
        assert services.unread_total(db, applicant) == 2

        # Opening the page directly consumes everything on it.
        assert services.unread_for(db, app, applicant, consume=True).count == 2
        db.commit()
        assert not services.unread_for(db, app, applicant, consume=False)
        assert services.unread_counts(db, applicant, [app.id]) == {}

        # A later reply from the specialist is new again; the applicant's own reply never is.
        services.add_comment(db, app, sarah, "brand_name", "Thanks, resolved.")
        db.commit()
        assert services.unread_counts(db, applicant, [app.id]) == {app.id: 1}
        assert services.unread_counts(db, sarah, [app.id]) == {app.id: 1}  # "The left one."

    def test_decisions_and_resubmissions_notify_the_other_side(self, db, users):
        app, applicant = make_app(db, users, "old-tom-title-case-warning")
        sarah = users["sarah.chen@ttb.gov"]
        services.submit(db, app, applicant)
        db.commit()
        # A submission is queue work, not a message: nothing is unread for the specialist.
        assert services.unread_counts(db, sarah, [app.id]) == {}

        draft = services.draft_correction(app, use_ai=False)
        services.decide(db, app, sarah, "request_correction", notice_body=draft.body)
        db.commit()
        unread = services.unread_for(db, app, applicant, consume=False)
        assert unread.notice and unread.fields and not unread.status  # notice + field comments
        services.mark_seen(db, app, applicant)
        db.commit()

        services.resubmit(
            db,
            app,
            applicant,
            services.to_application_data(app),
            DemoExtractor(),
            message="Fixed the heading.",
        )
        db.commit()
        # The resubmission itself plus the applicant's message that came with it.
        assert services.unread_counts(db, sarah, [app.id]) == {app.id: 2}
        services.mark_seen(db, app, sarah)
        services.decide(db, app, sarah, "approve")
        db.commit()
        assert services.unread_counts(db, applicant, [app.id]) == {app.id: 1}

    def test_activity_feed_lists_the_other_side_newest_first(self, db, users):
        app, applicant = make_app(db, users, "old-tom-title-case-warning")
        sarah = users["sarah.chen@ttb.gov"]
        services.submit(db, app, applicant)
        draft = services.draft_correction(app, use_ai=False)
        services.decide(db, app, sarah, "request_correction", notice_body=draft.body)
        services.add_comment(db, app, applicant, "health_warning", "Fixing the heading now.")
        db.commit()

        feed = services.activity_feed(db, applicant)
        kinds = [i.kind for i in feed]
        # Notice plus one comment per flagged field; the applicant's own reply is absent, and
        # the correction-requested status event is not repeated next to the notice.
        assert "notice" in kinds and "comment" in kinds and "status" not in kinds
        assert all(i.unread for i in feed)
        assert feed[0].anchor in ("notice", "field-health_warning")
        assert [i.kind for i in services.activity_feed(db, sarah)] == ["comment"]

        assert services.mark_all_seen(db, applicant) == len(feed)
        db.commit()
        assert not any(i.unread for i in services.activity_feed(db, applicant))
        assert services.unread_total(db, applicant) == 0

    def test_opening_one_inbox_item_leaves_the_others_unread(self, db, users):
        app, applicant = make_app(db, users)
        sarah = users["sarah.chen@ttb.gov"]
        services.submit(db, app, applicant)
        for n in range(3):
            services.add_comment(db, app, sarah, "general", f"Question {n}")
        db.commit()
        feed = services.activity_feed(db, applicant)
        assert len(feed) == 3 and services.unread_total(db, applicant) == 3

        opened, anchor = services.open_item(db, applicant, "comment", feed[0].item_id)
        db.commit()
        assert opened.id == app.id and anchor == "thread-host-general"
        assert services.unread_total(db, applicant) == 2
        assert [i.unread for i in services.activity_feed(db, applicant)] == [False, True, True]
        # Landing on the page through the inbox link keeps the other two new...
        assert services.unread_for(db, app, applicant, consume=False).count == 2
        # ...while opening the application from a list consumes them.
        assert services.unread_for(db, app, applicant, consume=True).count == 2
        db.commit()
        assert services.unread_total(db, applicant) == 0
        with pytest.raises(services.WorkflowError):
            services.open_item(db, applicant, "comment", "nope")


class TestLabelSets:
    def test_front_and_back_panels_are_one_version(self, db, users):
        applicant = users["maria@alvarezlabels.com"]
        data = ApplicationData.model_validate(sample("old-tom-bourbon")["application"])
        app = services.create_draft(
            db,
            applicant,
            data,
            [
                (sample_bytes("old-tom-bourbon"), "front.jpg"),
                (sample_bytes("old-tom-title-case-warning"), "back.jpg"),
            ],
        )
        assert [(i.version, i.panel, i.panel_label) for i in app.current_images] == [
            (1, 1, "Front"),
            (1, 2, "Back"),
        ]
        assert app.current_image.filename == "front.jpg"
        extractor = FixtureExtractor(
            LabelExtraction.model_validate(sample("old-tom-bourbon")["extraction"])
        )
        run = services.record_run(db, app, extractor, "precheck")
        assert run.image_version == 1 and run.recommendation == "approve"
        assert len(extractor.calls[0]) == 2  # both panels went to the extractor

    def test_resubmission_starts_a_new_version(self, db, users):
        app, applicant = make_app(db, users, "old-tom-abv-mismatch")
        sarah = users["sarah.chen@ttb.gov"]
        services.submit(db, app, applicant)
        services.decide(db, app, sarah, "request_correction", notice_body="fix")
        fixed = ApplicationData.model_validate(sample("old-tom-bourbon")["application"])
        services.resubmit(
            db,
            app,
            applicant,
            fixed,
            DemoExtractor(),
            uploads=[
                (sample_bytes("old-tom-bourbon"), "front-v2.jpg"),
                (sample_bytes("stones-throw-wine"), "back-v2.jpg"),
            ],
        )
        assert app.current_version == 2
        assert [i.filename for i in app.current_images] == ["front-v2.jpg", "back-v2.jpg"]
        assert [v for v, _ in app.images_by_version()] == [1, 2]
        assert app.latest_run.image_version == 2

    def test_too_many_panels_is_rejected(self, db, users):
        applicant = users["maria@alvarezlabels.com"]
        data = ApplicationData.model_validate(sample("old-tom-bourbon")["application"])
        with pytest.raises(UnreadableImageError, match="at most 4"):
            services.create_draft(
                db,
                applicant,
                data,
                [(sample_bytes("old-tom-bourbon"), f"{n}.jpg") for n in range(5)],
            )

    def test_batch_image_column_accepts_several_names(self):
        assert services.split_image_names("front.jpg; back.jpg") == ["front.jpg", "back.jpg"]
        assert services.split_image_names("only.png") == ["only.png"]
        assert services.split_image_names("") == []


class TestQueue:
    def test_queue_tabs_and_stats(self, db, users):
        sarah = users["sarah.chen@ttb.gov"]
        clean, applicant = make_app(db, users, "old-tom-bourbon")
        bad, _ = make_app(db, users, "old-tom-abv-mismatch")
        fixed, _ = make_app(db, users, "old-tom-title-case-warning")
        for app in (clean, bad, fixed):
            services.submit(db, app, applicant)
        services.decide(db, bad, sarah, "request_correction", notice_body="please fix")
        db.commit()

        assert [a.id for a in services.queue(db, "ready")] == [clean.id]
        assert [a.id for a in services.queue(db, "review")] == [fixed.id]
        assert [a.id for a in services.queue(db, "corrections")] == [bad.id]
        assert {a.id for a in services.queue(db, "open")} == {clean.id, fixed.id}
        stats = services.queue_stats(db)
        assert (stats.open, stats.ready, stats.review, stats.corrections) == (2, 1, 1, 1)

    def test_bulk_approve_only_takes_clean_open_applications(self, db, users):
        sarah = users["sarah.chen@ttb.gov"]
        clean, applicant = make_app(db, users, "old-tom-bourbon")
        bad, _ = make_app(db, users, "old-tom-abv-mismatch")
        services.submit(db, clean, applicant)
        services.submit(db, bad, applicant)
        assert services.bulk_approve(db, sarah, [clean.id, bad.id, "missing"]) == 1
        assert (
            clean.status == ApplicationStatus.APPROVED and bad.status == ApplicationStatus.SUBMITTED
        )


class TestBatch:
    def _zip(self, names: list[str]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name in names:
                zf.writestr(name, sample_bytes(name.rsplit(".", 1)[0]))
        return buf.getvalue()

    def _csv(self, rows: list[dict]) -> bytes:
        header = ",".join(services.BATCH_COLUMNS)
        lines = [header]
        for r in rows:
            lines.append(",".join(f'"{r.get(c, "")}"' for c in services.BATCH_COLUMNS))
        return "\n".join(lines).encode()

    def test_parse_and_process(self, session_factory, users, db):
        rows = []
        for sid in ("old-tom-bourbon", "old-tom-abv-mismatch"):
            row = dict(sample(sid)["application"])
            row["image"] = f"{sid}.png"
            row["is_import"] = "true" if row["is_import"] else "false"
            rows.append(row)
        rows.append(
            {
                "image": "missing.png",
                "beverage_type": "wine",
                "brand_name": "Ghost",
                "class_type": "Red",
            }
        )
        parsed = services.parse_batch(
            self._csv(rows), self._zip(["old-tom-bourbon.png", "old-tom-abv-mismatch.png"])
        )
        assert parsed.errors == [] and len(parsed.rows) == 3 and len(parsed.images) == 2

        batch = services.create_batch(db, users["maria@alvarezlabels.com"], "peak.csv", parsed)
        db.commit()
        services.process_batch(session_factory, batch.id, parsed, DemoExtractor())

        with session_factory() as fresh:
            from labelverify.web.models import Batch

            done = fresh.get(Batch, batch.id)
            assert done.status == "done" and (done.completed, done.failed) == (2, 1)
            statuses = {i.row_number: i.status for i in done.items}
            assert statuses == {1: "done", 2: "done", 3: "error"}
            assert "not found in the zip" in done.items[2].error
            summary = services.batch_summary(done)
            assert (
                summary["approve"] == 1
                and summary["request_correction"] == 1
                and summary["error"] == 1
            )
            assert all(
                i.application.status == ApplicationStatus.SUBMITTED
                for i in done.items
                if i.application
            )

    def test_slow_reads_hold_no_database_lock_and_the_batch_always_finishes(
        self, session_factory, users, db, extraction
    ):
        """Reproduces the production failure: a real model read takes seconds, five rows
        run at once, and SQLite allows one writer. The read must happen with no
        transaction open, and a crashing row must not leave the batch 'working'."""
        import sqlite3
        import threading
        import time

        from labelverify.web.models import Batch

        lock_errors: list[str] = []
        calls = {"n": 0}
        counter_lock = threading.Lock()

        class SlowReader:
            """A reader that takes a while and, during the wait, proves another connection
            can still write. The seventh row blows up with a non-extraction error."""

            name = "slow"

            def extract(self, image, media_type):
                return self.extract_panels([(image, media_type)])

            def extract_panels(self, panels):
                with counter_lock:
                    calls["n"] += 1
                    n = calls["n"]
                # Probe while this row's read is in progress. A worker that kept its
                # transaction open across the read (the original bug) holds the write lock
                # for the whole 0.25 s, so a 0.2 s probe fails; another worker's bookkeeping
                # commit lasts milliseconds and the probe outwaits it.
                path = str(session_factory.kw["bind"].url).removeprefix("sqlite:///")
                conn = sqlite3.connect(path, timeout=0.2)
                try:
                    conn.execute("UPDATE users SET organization = organization WHERE 1 = 0")
                    conn.commit()
                except sqlite3.OperationalError as exc:
                    lock_errors.append(str(exc))
                finally:
                    conn.close()
                time.sleep(0.25)
                if n == 7:
                    raise RuntimeError("reader crashed on this row")
                return extraction.model_copy(deep=True)

        rows = []
        for i in range(8):
            row = dict(sample("old-tom-bourbon")["application"])
            row["image"] = "old-tom-bourbon.png"
            row["is_import"] = "false"
            row["brand_name"] = f"Row {i + 1}"
            rows.append(row)
        parsed = services.parse_batch(self._csv(rows), self._zip(["old-tom-bourbon.png"]))
        assert parsed.errors == []
        batch = services.create_batch(db, users["maria@alvarezlabels.com"], "slow.csv", parsed)
        db.commit()

        services.process_batch(session_factory, batch.id, parsed, SlowReader())

        assert lock_errors == [], f"a write was blocked during a model read: {lock_errors}"
        with session_factory() as fresh:
            done = fresh.get(Batch, batch.id)
            assert done.status == "done" and done.finished_at is not None
            assert (done.completed, done.failed) == (7, 1)
            crashed = [i for i in done.items if i.status == "error"]
            assert len(crashed) == 1 and "reader crashed" in crashed[0].error
            assert all(i.status != "pending" for i in done.items)

    def test_parse_errors(self):
        parsed = services.parse_batch(b"brand_name\nX", b"not a zip")
        assert any("missing required column" in e for e in parsed.errors)
        parsed = services.parse_batch(self._csv([]), b"not a zip")
        assert any("no data rows" in e for e in parsed.errors)
        assert any("not a valid .zip" in e for e in parsed.errors)


def test_seed_populates_a_realistic_queue(db):
    seed(db)
    stats = services.queue_stats(db)
    assert stats.open >= 3
    assert stats.corrections >= 1
    assert len(services.queue(db, "approved")) >= 1
    from labelverify.web.models import Application, ApplicationStatus

    rejected = [a for a in db.query(Application) if a.status == ApplicationStatus.REJECTED]
    assert len(rejected) == 1 and rejected[0].notices and "rejected" in rejected[0].notices[-1].body
    seed(db)  # idempotent
    assert services.queue_stats(db) == stats


def test_init_db_adds_columns_to_a_database_from_the_previous_release(tmp_path):
    """Databases created before label sets existed gain version/panel columns on startup."""
    import sqlite3

    from sqlalchemy import text

    from labelverify.web.db import init_db, make_engine, make_session_factory
    from labelverify.web.models import Application

    path = tmp_path / "old.db"
    engine = make_engine(f"sqlite:///{path}")
    init_db(engine)
    with make_session_factory(engine)() as db:
        seed(db)
    engine.dispose()
    with sqlite3.connect(path) as con:  # simulate the old schema
        con.execute("ALTER TABLE label_images DROP COLUMN version")
        con.execute("ALTER TABLE label_images DROP COLUMN panel")
        con.execute("ALTER TABLE verification_runs DROP COLUMN image_version")
        for column in (
            "extraction_json",
            "extraction_version",
            "extraction_ms",
            "extraction_extractor",
            "beverage_type_inferred",
            "health_warning",
            "age_statement",
        ):
            con.execute(f"ALTER TABLE applications DROP COLUMN {column}")
        assert "version" not in [r[1] for r in con.execute("PRAGMA table_info(label_images)")]

    engine = make_engine(f"sqlite:///{path}")
    init_db(engine)  # second start on the same file
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(label_images)"))]
        assert "version" in cols and "panel" in cols
        app_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(applications)"))]
        assert "extraction_json" in app_cols and "extraction_version" in app_cols
        assert "health_warning" in app_cols and "age_statement" in app_cols
    with make_session_factory(engine)() as db:
        app = db.query(Application).first()
        assert [(i.version, i.panel) for i in app.current_images] == [(1, 1)]
        assert app.latest_run.image_version == 1


def test_secret_key_is_generated_once_and_reused(tmp_path, monkeypatch):
    from labelverify.web.auth import secret_key

    monkeypatch.delenv("SECRET_KEY", raising=False)
    first = secret_key(tmp_path)
    assert len(first) == 64 and (tmp_path / ".secret_key").read_text() == first
    assert secret_key(tmp_path) == first  # survives a restart
    monkeypatch.setenv("SECRET_KEY", "configured")
    assert secret_key(tmp_path) == "configured"  # the environment always wins


def test_label_carries_covers_every_label_only_checklist_item():
    """Every checklist item that is not a form field and not a specialist judgment has a
    value the step-2 checklist can show, keyed by the rulebook's field name."""
    from labelverify.engine.extractors.demo import load_manifest
    from labelverify.engine.models import LabelExtraction
    from labelverify.engine.rules import all_rules

    form_items = {
        "brand_name",
        "class_type",
        "alcohol_content",
        "net_contents",
        "producer_name",
        "producer_address",
        "country_of_origin",
    }
    label_only = {
        i["field"]
        for entry in all_rules()
        for i in entry["checklist"]
        if i["field"] not in form_items and i["checked_by"] == "engine"
    }
    wine = next(s for s in load_manifest() if s["id"] == "stones-throw-wine")
    found = services.label_carries(LabelExtraction.model_validate(wine["extraction"]))
    assert label_only <= set(found)
    assert found["appellation"] == "NAPA VALLEY" and found["vintage_year"] == "2021"
    assert found["estate_bottled"] == "ESTATE BOTTLED" and found["sulfite_declaration"]
    assert found["age_statement"] is None and found["health_warning"]
