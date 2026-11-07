from datetime import timedelta
from unittest import mock

from sqlalchemy import select

from ific_guard import events
from ific_guard.config import get_settings
from ific_guard.db import session_scope, utcnow
from ific_guard.models import Artifact, Case, Event, Notification
from ific_guard.notifier import mailer
from ific_guard.notifier.runner import (
    MAX_SEND_ATTEMPTS,
    deliver_pending,
    handle_issue_screened,
    handle_ops_alert,
)


def _setup(severities=("high", "medium", "low")) -> int:
    with session_scope() as s:
        artifact = Artifact(domain="space", issue_no=3083, source_url="https://itu/x.zip", sha256="f" * 64, size_bytes=1, mdb_path="x")
        s.add(artifact)
        s.flush()
        for i, severity in enumerate(severities):
            s.add(Case(fingerprint=str(i), domain="space", rule_id="r", severity=severity, title=f"Case {severity}",
                       adm="USA", network="SAT", notice_ids=["1"], details={}, material_hash="m", first_issue=3083,
                       last_issue=3083, changed_artifact_id=artifact.id, source_sha256="f" * 64))
        return artifact.id


def test_digest_is_ranked_filtered_and_says_these_are_not_confirmed_breaches():
    artifact_id = _setup()
    handle_issue_screened({"artifact_id": artifact_id})
    with session_scope() as s:
        item = s.scalars(select(Notification)).one()
    assert item.recipients == ["expert@example.org"]
    assert item.subject == "BR IFIC 3083 (space): 2 possible concerns for review, 1 high"
    assert item.body.index("Case high") < item.body.index("Case medium")
    assert "Case low" not in item.body  # below IFIC_NOTIFY_MIN_SEVERITY
    assert "None is a confirmed breach" in item.body
    assert "f" * 64 in item.body  # provenance


def test_reprocessing_an_event_does_not_mail_twice():
    artifact_id = _setup()
    handle_issue_screened({"artifact_id": artifact_id})
    handle_issue_screened({"artifact_id": artifact_id})
    assert deliver_pending() == 1
    assert deliver_pending() == 0
    assert len(list((get_settings().data_dir / "outbox").glob("*.eml"))) == 1


def test_no_new_cases_means_no_mail():
    artifact_id = _setup(severities=())
    handle_issue_screened({"artifact_id": artifact_id})
    with session_scope() as s:
        assert s.scalars(select(Notification)).first() is None


def test_failed_send_backs_off_then_ends_as_failed(monkeypatch):
    handle_ops_alert({"dedupe_key": "x", "subject": "s", "body": "b"})
    monkeypatch.setattr(mailer, "send", mock.Mock(side_effect=OSError("smtp down")))
    assert deliver_pending() == 0
    assert deliver_pending() == 0  # backed off, so not retried yet
    assert mailer.send.call_count == 1
    for _ in range(MAX_SEND_ATTEMPTS - 1):
        with session_scope() as s:
            s.scalars(select(Notification)).one().next_attempt_at = utcnow() - timedelta(seconds=1)
        deliver_pending()
    with session_scope() as s:
        item = s.scalars(select(Notification)).one()
    assert item.status == "failed" and "smtp down" in item.last_error


def test_smtp_backend_uses_tls_login_and_message(monkeypatch):
    monkeypatch.setenv("IFIC_EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("IFIC_SMTP_USER", "user")
    monkeypatch.setenv("IFIC_SMTP_PASSWORD", "secret")
    get_settings.cache_clear()
    with mock.patch("smtplib.SMTP") as smtp:
        mailer.send(get_settings(), ["a@example.org"], "Subject", "Body", "k")
    server = smtp.return_value.__enter__.return_value
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("user", "secret")
    assert server.send_message.call_args.args[0]["To"] == "a@example.org"


def test_outbox_claims_once_retries_with_backoff_then_alerts():
    with session_scope() as s:
        events.publish(s, events.ISSUE_FETCHED, {"artifact_id": 1})
    event = events.claim(events.ISSUE_FETCHED)
    assert event.attempts == 1
    assert events.claim(events.ISSUE_FETCHED) is None  # leased

    for _ in range(events.MAX_ATTEMPTS - 1):
        events.fail(event.id, "boom")
        assert events.claim(events.ISSUE_FETCHED) is None  # backing off
        with session_scope() as s:
            s.get_one(Event, event.id).available_at = utcnow() - timedelta(seconds=1)
        event = events.claim(events.ISSUE_FETCHED)
    events.fail(event.id, "boom")
    with session_scope() as s:
        assert s.get_one(Event, event.id).status == "dead"
        alert = s.scalars(select(Event).where(Event.topic == events.OPS_ALERT)).one()
    assert "boom" in alert.payload["body"]


def test_expired_lease_is_retried_after_a_worker_crash():
    with session_scope() as s:
        events.publish(s, events.ISSUE_FETCHED, {"artifact_id": 1})
    event = events.claim(events.ISSUE_FETCHED)
    with session_scope() as s:
        s.get_one(Event, event.id).locked_until = utcnow() - timedelta(seconds=1)
    assert events.claim(events.ISSUE_FETCHED).attempts == 2
