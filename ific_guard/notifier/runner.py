"""Turns events into notifications, then delivers them.

These are two steps so that an SMTP outage never blocks event processing: a notification
waits in its table with its own retries. dedupe_key stops a re-processed event mailing twice.
"""
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import case as sql_case
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import Artifact, Case, Notification
from . import mailer

log = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 6
_ORDER = {"low": 0, "medium": 1, "high": 2}


def _enqueue(key: str, kind: str, recipients: list[str], subject: str, body: str) -> bool:
    with session_scope() as s:
        if s.scalars(select(Notification.id).where(Notification.dedupe_key == key)).first():
            return False
        s.add(Notification(dedupe_key=key, kind=kind, recipients=recipients, subject=subject, body=body))
    return True


def render_digest(artifact: Artifact, cases: list[Case], max_cases: int, base_url: str) -> tuple[str, str]:
    high = sum(c.severity == "high" for c in cases)
    label = f"BR IFIC {artifact.issue_no} ({artifact.domain})"
    subject = f"{label}: {len(cases)} possible concerns for review, {high} high"
    lines = [
        f"Automated screening of {label} found {len(cases)} new or changed possible concerns for Finland.",
        "These are screening results for expert review. None is a confirmed breach of a regulation.",
        "",
    ]
    for c in cases[:max_cases]:
        lines += [
            f"[{c.severity.upper()}] {c.title}",
            f"  Rule: {c.rule_id}   Notices: {', '.join(c.notice_ids)}",
            f"  Comment deadline (estimated, please confirm): {c.comment_deadline or 'n/a'}",
            f"  Review: {base_url.rstrip('/')}/cases/{c.id}",
            "",
        ]
    if len(cases) > max_cases:
        lines += [f"...and {len(cases) - max_cases} more: {base_url.rstrip('/')}/cases", ""]
    lines += [f"Source: {artifact.source_url}", f"SHA-256: {artifact.sha256}"]
    return subject, "\n".join(lines)


def handle_issue_screened(payload: dict[str, Any]) -> None:
    settings = get_settings()
    artifact_id = int(payload["artifact_id"])
    minimum = _ORDER[settings.notify_min_severity]
    wanted = [k for k, v in _ORDER.items() if v >= minimum]
    with session_scope() as s:
        artifact = s.get_one(Artifact, artifact_id)
        rank = sql_case({"high": 0, "medium": 1, "low": 2}, value=Case.severity)
        cases = list(
            s.scalars(
                select(Case)
                .where(Case.changed_artifact_id == artifact_id, Case.severity.in_(wanted))
                .order_by(rank, Case.comment_deadline, Case.id)
            )
        )
        if not cases:
            log.info("issue %s: nothing new to notify", artifact.issue_no)
            return
        subject, body = render_digest(artifact, cases, settings.digest_max_cases, settings.public_base_url)
    # the case set is part of the key: a re-screen that changes nothing cannot mail twice
    key = f"digest:{artifact_id}:{len(cases)}:{max(c.id for c in cases)}"
    _enqueue(key, "digest", settings.mail_to, subject, body)


def handle_ops_alert(payload: dict[str, Any]) -> None:
    settings = get_settings()
    _enqueue(
        f"ops:{payload['dedupe_key']}",
        "ops",
        settings.ops_mail_to or settings.mail_to,
        str(payload["subject"]),
        str(payload["body"]),
    )


def deliver_pending() -> int:
    """Sends what is due. A failed send backs off, then ends as `failed`, visible in GET /notifications."""
    settings = get_settings()
    sent = 0
    with session_scope() as s:
        due = list(
            s.scalars(
                select(Notification)
                .where(Notification.status == "pending", Notification.next_attempt_at <= utcnow())
                .order_by(Notification.id)
            )
        )
    for item in due:
        try:
            mailer.send(settings, item.recipients, item.subject, item.body, item.dedupe_key)
        except Exception as exc:
            log.warning("could not send notification %s: %s", item.id, exc)
            with session_scope() as s:
                row = s.get_one(Notification, item.id)
                row.attempts += 1
                row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                if row.attempts >= MAX_SEND_ATTEMPTS:
                    row.status = "failed"
                else:
                    row.next_attempt_at = utcnow() + timedelta(minutes=2**row.attempts)
            continue
        with session_scope() as s:
            row = s.get_one(Notification, item.id)
            row.status, row.sent_at, row.attempts, row.last_error = "sent", utcnow(), row.attempts + 1, None
        sent += 1
    return sent
