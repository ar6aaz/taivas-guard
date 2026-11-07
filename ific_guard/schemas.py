"""API response and request shapes."""
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ArtifactOut(_Orm):
    id: int
    domain: str
    issue_no: int
    published_on: date | None
    source_url: str
    sha256: str
    size_bytes: int
    fetched_at: datetime
    screen_status: str
    screened_at: datetime | None


class UploadOut(BaseModel):
    artifact: ArtifactOut
    new: bool  # false: this exact file was already stored


class SyncRunOut(_Orm):
    id: int
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    discovered: int
    fetched: int
    errors: list[str]


class CaseSummary(_Orm):
    id: int
    domain: str
    rule_id: str
    severity: str
    title: str
    adm: str | None
    network: str | None
    first_issue: int
    last_issue: int
    comment_deadline: date | None
    review_status: str


class CaseOut(CaseSummary):
    notice_ids: list[str]
    details: dict[str, Any]
    source_sha256: str
    review_note: str | None
    created_at: datetime
    updated_at: datetime


class CaseReview(BaseModel):
    review_status: Literal["pending", "accepted", "rejected"]
    review_note: str | None = None


class NotificationOut(_Orm):
    id: int
    kind: str
    recipients: list[str]
    subject: str
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None
