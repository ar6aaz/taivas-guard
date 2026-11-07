"""Outbox-based messaging between services. Delivery is at-least-once, so handlers must be idempotent.

Each topic has exactly one consuming service:
    issue.fetched   sync -> screening
    issue.screened  screening -> notifier
    ops.alert       any service -> notifier
"""
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .db import session_scope, utcnow
from .models import Event

log = logging.getLogger(__name__)

ISSUE_FETCHED = "issue.fetched"
ISSUE_SCREENED = "issue.screened"
OPS_ALERT = "ops.alert"

MAX_ATTEMPTS = 5
LEASE = timedelta(minutes=30)  # longer than the slowest screening run


def publish(session: Session, topic: str, payload: dict[str, Any]) -> None:
    """Add an event to the caller's transaction, so it commits with the change it announces."""
    session.add(Event(topic=topic, payload=payload))


def claim(topic: str) -> Event | None:
    """Take the oldest ready event. A lease that expired means its worker died, so it is retried."""
    now = utcnow()
    with session_scope() as s:
        event = s.scalars(
            select(Event)
            .where(
                Event.topic == topic,
                or_(
                    (Event.status == "pending") & (Event.available_at <= now),
                    (Event.status == "processing") & (Event.locked_until < now),
                ),
            )
            .order_by(Event.id)
            .limit(1)
        ).first()
        if event is None:
            return None
        # compare-and-set on attempts: if another worker got there first, nothing is updated
        won = s.execute(
            update(Event)
            .where(Event.id == event.id, Event.attempts == event.attempts)
            .values(status="processing", attempts=event.attempts + 1, locked_until=now + LEASE)
        ).rowcount
        if not won:
            return None
        s.refresh(event)
        return event


def complete(event_id: int) -> None:
    with session_scope() as s:
        s.execute(update(Event).where(Event.id == event_id).values(status="done", done_at=utcnow(), last_error=None))


def fail(event_id: int, error: str) -> None:
    with session_scope() as s:
        event = s.get_one(Event, event_id)
        event.last_error = error[:2000]
        if event.attempts < MAX_ATTEMPTS:
            event.status = "pending"
            event.available_at = utcnow() + timedelta(minutes=2**event.attempts)
            return
        event.status = "dead"
        log.error("event %s (%s) is dead: %s", event.id, event.topic, error)
        if event.topic != OPS_ALERT:  # an alert that cannot be delivered must not raise another alert
            publish(
                s,
                OPS_ALERT,
                {
                    "dedupe_key": f"dead-event:{event.id}",
                    "subject": f"IFIC Guard: {event.topic} failed after {event.attempts} attempts",
                    "body": f"Event {event.id} with payload {event.payload} stopped retrying.\n\nLast error:\n{error}",
                },
            )
