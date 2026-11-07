"""Runs rules over one fetched issue and saves the results as cases."""
import hashlib
import json
import logging
from dataclasses import asdict
from typing import Any

from sqlalchemy import delete, select

from .. import events
from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import Artifact, Case, CaseOccurrence
from ..sync.sources import SPACE, TERRESTRIAL
from .rules import Hit, Rule, space_rules
from .space import MdbReader, SpaceIssue, TableReader

log = logging.getLogger(__name__)


def _digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def save_hits(artifact_id: int, hits: list[Hit], domain: str = SPACE) -> tuple[int, int]:
    """Upserts cases. Returns (opened, changed); an unchanged re-run reports (0, 0)."""
    opened = changed = 0
    with session_scope() as s:
        artifact = s.get_one(Artifact, artifact_id)
        for hit in hits:
            fingerprint = _digest(domain, hit.rule_id, hit.family_id)
            material = _digest(hit.severity, hit.details)
            case = s.scalars(select(Case).where(Case.fingerprint == fingerprint)).first()
            if case is None:
                opened += 1
                s.add(
                    Case(
                        fingerprint=fingerprint,
                        domain=domain,
                        rule_id=hit.rule_id,
                        severity=hit.severity,
                        title=hit.title,
                        adm=hit.adm,
                        network=hit.network,
                        notice_ids=[str(n) for n in hit.notice_ids],
                        details=hit.details,
                        material_hash=material,
                        first_issue=artifact.issue_no,
                        last_issue=artifact.issue_no,
                        changed_artifact_id=artifact.id,
                        source_sha256=artifact.sha256,
                        comment_deadline=hit.comment_deadline,
                    )
                )
                continue
            case.first_issue = min(case.first_issue, artifact.issue_no)  # issues can arrive out of order by upload
            case.last_issue = max(case.last_issue, artifact.issue_no)
            if case.material_hash != material and artifact.issue_no >= case.last_issue:
                changed += 1
                case.material_hash, case.details, case.severity = material, hit.details, hit.severity
                case.title, case.source_sha256 = hit.title, artifact.sha256
                case.notice_ids = sorted({*case.notice_ids, *map(str, hit.notice_ids)})
                case.comment_deadline = hit.comment_deadline
                case.changed_artifact_id = artifact.id
                case.review_status = "pending"  # the content changed, so an earlier decision no longer covers it
        # Replace only this artifact's latest successful result, in the same
        # transaction as case changes. Empty results clear obsolete occurrences;
        # any failure rolls both changes back. Other issues/revisions are untouched.
        s.flush()
        s.execute(delete(CaseOccurrence).where(CaseOccurrence.artifact_id == artifact_id))
        for hit in hits:
            fingerprint = _digest(domain, hit.rule_id, hit.family_id)
            case_id = s.scalars(select(Case.id).where(Case.fingerprint == fingerprint)).one()
            snapshot = asdict(hit)
            snapshot["comment_deadline"] = hit.comment_deadline.isoformat() if hit.comment_deadline else None
            s.add(CaseOccurrence(artifact_id=artifact_id, case_id=case_id, hit=snapshot))
    return opened, changed


def screen_artifact(
    artifact_id: int, reader: TableReader | None = None, rules: list[Rule] | None = None
) -> dict[str, Any]:
    settings = get_settings()
    with session_scope() as s:
        artifact = s.get_one(Artifact, artifact_id)
        domain, issue_no, mdb_path, sha256 = artifact.domain, artifact.issue_no, artifact.mdb_path, artifact.sha256

    if domain == TERRESTRIAL:
        from ..terrestrial.adapter import TerrestrialIssue
        from ..terrestrial.rule_field_strength import terrestrial_rules

        issue = TerrestrialIssue(reader or MdbReader(mdb_path), expected_issue=issue_no)
        active = rules if rules is not None else terrestrial_rules(settings, issue_no, sha256)
        hits = [hit for rule in active for hit in rule.run(issue)]
    else:
        issue = SpaceIssue(reader or MdbReader(mdb_path))
        hits = [hit for rule in (rules if rules is not None else space_rules(settings)) for hit in rule.run(issue)]
        for hit in hits:
            hit.details.setdefault("database_version", issue.database_version)
    opened, changed = save_hits(artifact_id, hits, domain)
    status = "done"

    with session_scope() as s:
        artifact = s.get_one(Artifact, artifact_id)
        artifact.screen_status, artifact.screened_at = status, utcnow()
        events.publish(
            s, events.ISSUE_SCREENED, {"artifact_id": artifact_id, "status": status, "opened": opened, "changed": changed}
        )
    log.info("screened %s issue %s: %s opened, %s changed (%s)", domain, issue_no, opened, changed, status)
    return {"artifact_id": artifact_id, "status": status, "opened": opened, "changed": changed}


def handle_issue_fetched(payload: dict[str, Any]) -> None:
    artifact_id = int(payload["artifact_id"])
    try:
        screen_artifact(artifact_id)
    except Exception:
        with session_scope() as s:
            s.get_one(Artifact, artifact_id).screen_status = "failed"
        raise
