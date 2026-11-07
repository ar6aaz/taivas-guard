"""Notifier service: emails experts about new possible concerns and operators about failures."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from .. import events
from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import Notification
from ..schemas import NotificationOut
from ..service import consume, create_app, every, require_api_key
from .runner import _enqueue, deliver_pending, handle_issue_screened, handle_ops_alert

router = APIRouter()


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(state: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100):
    query = select(Notification).order_by(Notification.id.desc()).limit(limit)
    if state:
        query = query.where(Notification.status == state)
    with session_scope() as s:
        return list(s.scalars(query))


@router.post("/notifications/test", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_api_key)])
def send_test() -> dict[str, str]:
    """Queues a test mail to the configured recipients, to confirm the mail setup works."""
    settings = get_settings()
    _enqueue(
        f"test:{utcnow().isoformat()}",
        "test",
        settings.ops_mail_to or settings.mail_to,
        "IFIC Guard test message",
        "If you can read this, IFIC Guard can send you email.",
    )
    return {"status": "queued"}


async def _screened() -> None:
    await consume(events.ISSUE_SCREENED, handle_issue_screened)


async def _alerts() -> None:
    await consume(events.OPS_ALERT, handle_ops_alert)


async def _delivery() -> None:
    await every(get_settings().worker_poll_s, deliver_pending)


app = create_app("ific-notifier", router, [_screened, _alerts, _delivery])
