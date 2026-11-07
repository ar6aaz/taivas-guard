"""Operational tables. Analytical notice data stays in the DuckDB store from schema.sql."""
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


class Artifact(Base):
    """One downloaded IFIC file. A republished issue is a new row and is never overwritten."""

    __tablename__ = "artifact"
    __table_args__ = (UniqueConstraint("domain", "issue_no", "sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(16))  # space | terrestrial
    issue_no: Mapped[int] = mapped_column(Integer, index=True)
    published_on: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)  # uploaded databases can pass 2 GB
    etag: Mapped[str | None] = mapped_column(Text)
    mdb_path: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    screen_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|done|no_rules|failed
    screened_at: Mapped[datetime | None] = mapped_column(DateTime)


class SyncRun(Base):
    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))  # schedule | manual
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|partial|failed
    discovered: Mapped[int] = mapped_column(Integer, default=0)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)


class Case(Base):
    """A reviewable possible concern: one rule against one notice family, across issues."""

    __tablename__ = "case_"
    __table_args__ = (Index("ix_case_changed", "changed_artifact_id", "severity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    domain: Mapped[str] = mapped_column(String(16))
    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(8), index=True)  # high|medium|low
    title: Mapped[str] = mapped_column(Text)
    adm: Mapped[str | None] = mapped_column(String(8))
    network: Mapped[str | None] = mapped_column(Text)
    notice_ids: Mapped[list[str]] = mapped_column(JSON)
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    material_hash: Mapped[str] = mapped_column(String(64))
    first_issue: Mapped[int] = mapped_column(Integer)
    last_issue: Mapped[int] = mapped_column(Integer)
    changed_artifact_id: Mapped[int] = mapped_column(Integer)  # the run that opened or materially changed it
    source_sha256: Mapped[str] = mapped_column(String(64))
    comment_deadline: Mapped[date | None] = mapped_column(Date)
    review_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|accepted|rejected
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CaseOccurrence(Base):
    """Issue-local evidence from the latest successful screening of one artifact.

    Case is mutable review state across issues. This snapshot retains the exact
    hit even when a newer issue changes that case or an older issue arrives late.
    """

    __tablename__ = "case_occurrence"
    __table_args__ = (UniqueConstraint("artifact_id", "case_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifact.id"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("case_.id"), index=True)
    hit: Mapped[dict[str, Any]] = mapped_column(JSON)
    screened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(16))  # digest | ops | test
    recipients: Mapped[list[str]] = mapped_column(JSON)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|sent|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)


class Event(Base):
    """Transactional outbox. Written in the same commit as the change it announces."""

    __tablename__ = "event"
    __table_args__ = (Index("ix_event_claim", "topic", "status", "available_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    topic: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|processing|done|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(DateTime)
