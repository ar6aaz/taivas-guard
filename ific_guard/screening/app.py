"""Screening service: turns fetched issues into cases and serves them for expert review."""
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select

from .. import events
from ..db import session_scope
from ..llm import Draft, configured_model, draft, generate
from ..llm.template import CaseView
from ..models import Artifact, Case
from ..schemas import CaseOut, CaseReview, CaseSummary
from ..service import consume, create_app, require_api_key
from .engine import handle_issue_fetched

router = APIRouter()


@router.get("/cases", response_model=list[CaseSummary])
def list_cases(
    severity: str | None = None,
    rule_id: str | None = None,
    review_status: str | None = None,
    adm: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    query = select(Case).order_by(Case.comment_deadline.asc(), Case.id.desc()).limit(limit)
    for column, value in ((Case.severity, severity), (Case.rule_id, rule_id), (Case.review_status, review_status), (Case.adm, adm)):
        if value:
            query = query.where(column == value)
    with session_scope() as s:
        return list(s.scalars(query))


@router.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: int):
    with session_scope() as s:
        case = s.get(Case, case_id)
        if case is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No case {case_id}.")
        return case


@router.get("/cases/{case_id}/draft", response_model=Draft)
def get_case_draft(case_id: int, use_llm: bool = False, x_api_key: Annotated[str | None, Header()] = None):
    """An explanation and draft letter for expert review. Stores and sends nothing.

    Returns the deterministic template by default. `use_llm=true` can cost money, so it needs the API key.
    """
    if use_llm:
        require_api_key(x_api_key)
    with session_scope() as s:
        case = s.get(Case, case_id)
        if case is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No case {case_id}.")
        view = CaseView.of(case)
    return generate(view, configured_model()) if use_llm else draft(view)


@router.patch("/cases/{case_id}", response_model=CaseOut, dependencies=[Depends(require_api_key)])
def review_case(case_id: int, review: CaseReview):
    with session_scope() as s:
        case = s.get(Case, case_id)
        if case is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No case {case_id}.")
        case.review_status, case.review_note = review.review_status, review.review_note
        return case


@router.post(
    "/issues/{artifact_id}/screen", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_api_key)]
)
def rescreen(artifact_id: int) -> dict[str, str]:
    """Queues an issue again, for example after a rule changes."""
    with session_scope() as s:
        artifact = s.get(Artifact, artifact_id)
        if artifact is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No artifact {artifact_id}.")
        artifact.screen_status = "pending"
        events.publish(s, events.ISSUE_FETCHED, {"artifact_id": artifact_id})
    return {"status": "queued"}


async def _worker() -> None:
    await consume(events.ISSUE_FETCHED, handle_issue_fetched)


app = create_app("ific-screening", router, [_worker])
