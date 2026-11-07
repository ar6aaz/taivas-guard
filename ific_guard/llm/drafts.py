"""Explanations and draft letters for finished cases.

`draft(case)` works from case data alone. `generate(case, model)` lets a model rewrite that draft and
returns the template instead whenever the rewrite fails the guardrail. Neither stores or sends anything.
"""
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel

from .guardrail import Evidence, Violation, check_text
from .model import DraftModel, ModelError
from .retrieval import Passage, retrieve
from .template import (
    BANNER,
    CaseView,
    citations,
    draft_letter_body,
    evidence_values,
    explain_case,
    facts,
    is_calculated,
    levels_of,
    margin_of,
    reference_block,
    review_notes,
)

log = logging.getLogger(__name__)


class Draft(BaseModel):
    case_id: int
    status: Literal["draft_requires_expert_review"] = "draft_requires_expert_review"
    requires_expert_review: Literal[True] = True
    sent: Literal[False] = False
    banner: str = BANNER
    source: Literal["template", "llm"]
    model: str | None = None
    fallback_reason: str | None = None
    violations: list[Violation] = []
    method: str
    possible_concern_only: bool
    explanation: str
    subject: str
    letter: str
    facts: dict[str, Any]
    citations: list[dict[str, Any]]
    review_notes: list[str]
    passages_retrieved: list[str]
    passages_missing: list[str]


def _subject(view: CaseView) -> str:
    kind = "Calculated result" if is_calculated(view.details) else "Possible concern"
    ids = ", ".join(view.notice_ids)
    return f"{kind} for Finland: {view.network or 'network not named'} ({view.adm or 'administration not recorded'}), notice ID(s) {ids}, BR IFIC {view.issue_no}"


def _assemble(view: CaseView, body: str, passages: list[Passage]) -> str:
    """Code adds the banner and the reference data, so no rewrite can remove them."""
    addressee = f"To: Notifying administration, ITU code {view.adm or '[not recorded]'} [address to be added by the reviewing expert]"
    reference = reference_block(view, citations(view, passages))
    return "\n\n".join(
        [BANNER, addressee, f"Subject: {_subject(view)}", body, "[Signatory to be added by the reviewing expert]", f"Reference data:\n{reference}"]
    )


def _build(
    view: CaseView,
    passages: list[Passage],
    missing: list[str],
    body: str,
    explanation: str,
    source: Literal["template", "llm"],
    model_name: str | None = None,
    fallback_reason: str | None = None,
    violations: list[Violation] | None = None,
) -> Draft:
    calculated = is_calculated(view.details)
    return Draft(
        case_id=view.id,
        source=source,
        model=model_name,
        fallback_reason=fallback_reason,
        violations=violations or [],
        method=view.details["method"] if calculated else "screening",
        possible_concern_only=not ((margin_of(view.details) or 0) > 0),
        explanation=explanation,
        subject=_subject(view),
        letter=_assemble(view, body, passages),
        facts=facts(view),
        citations=citations(view, passages),
        review_notes=review_notes(view, passages, missing),
        passages_retrieved=[p.id for p in passages],
        passages_missing=missing,
    )


def evidence_for(view: CaseView, passages: list[Passage]) -> Evidence:
    evidence = Evidence().add(*evidence_values(view, passages))
    evidence.add_provisions(view.details.get("provisions", []))
    evidence.add_provisions(view.details.get("sections", []))
    return evidence


def build_prompt(view: CaseView, passages: list[Passage], body: str, explanation: str) -> str:
    case_json = json.dumps(facts(view) | {"assumptions": view.details.get("assumptions", [])}, indent=2, ensure_ascii=False)
    passage_text = "\n\n".join(f'<passage doc="{p.doc}" clause="{p.clause}">\n{p.text}\n</passage>' for p in passages)
    return (
        f"<case>\n{case_json}\n</case>\n\n<passages>\n{passage_text}\n</passages>\n\n"
        f"<template>\n<explanation>\n{explanation}\n</explanation>\n<letter_body>\n{body}\n</letter_body>\n</template>"
    )


def draft(case: Any, index: list[Passage] | None = None) -> Draft:
    """The deterministic draft. Needs no API key."""
    view = CaseView.of(case)
    passages, missing = retrieve(view.domain, view.details, index)
    return _build(view, passages, missing, draft_letter_body(view), explain_case(view), "template")


def explain(case: Any) -> str:
    return explain_case(CaseView.of(case))


def generate(case: Any, model: DraftModel | None, index: list[Passage] | None = None) -> Draft:
    """A model's rewrite if it passes the guardrail; otherwise the template, with the reason recorded."""
    view = CaseView.of(case)
    passages, missing = retrieve(view.domain, view.details, index)
    body, explanation = draft_letter_body(view), explain_case(view)

    def fallback(reason: str, violations: list[Violation] | None = None) -> Draft:
        log.warning("case %s: using the template draft: %s %s", view.id, reason, [v.model_dump() for v in violations or []])
        name = model.name if model else None
        return _build(view, passages, missing, body, explanation, "template", name, reason, violations)

    if model is None:
        return fallback("no language model is configured")
    try:
        output = model.rewrite(build_prompt(view, passages, body, explanation))
    except ModelError as exc:
        return fallback(str(exc))

    evidence = evidence_for(view, passages)
    calculated, margin, levels = is_calculated(view.details), margin_of(view.details), levels_of(view.details)
    violations = check_text(output.letter_body, evidence, calculated, margin, levels)
    violations += check_text(output.explanation, evidence, calculated, margin, levels)
    if violations:
        return fallback("the model's text failed the guardrail", violations)
    return _build(view, passages, missing, output.letter_body, output.explanation, "llm", model.name)
