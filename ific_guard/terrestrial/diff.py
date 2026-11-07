"""Interpret notified actions; missing rows in the next circular are not withdrawals."""
from dataclasses import dataclass

from .adapter import TerrestrialIssue


@dataclass(frozen=True)
class Change:
    terrakey: str
    adm: str
    kind: str  # new | modified | withdrawn | unknown
    target_ids: tuple[str, ...]
    present_in_previous: bool
    target_in_previous: bool
    basis: str


def diff_issues(previous: TerrestrialIssue, current: TerrestrialIssue) -> list[Change]:
    if current.issue_no != previous.issue_no + 1:
        raise ValueError("Terrestrial diff requires consecutive issue numbers.")
    changes = []
    for notice in current.notices.values():
        actions = {r.get("action") for r in notice.targets if r.get("action") in ("MODIFY", "SUPPRESS")}
        intent = notice.intent
        targets = tuple(sorted({str(r["target"]) for r in notice.targets if r.get("target") is not None}))
        if len(actions) > 1 or (actions and intent not in actions):
            kind, basis = "unknown", "Conflicting intent and target actions; expert review required."
        else:
            kind = {"ADD": "new", "MODIFY": "modified", "SUPPRESS": "withdrawn"}.get(intent, "unknown")
            basis = f"Notified intent {intent}; target actions {sorted(actions)}."
        changes.append(Change(
            notice.terrakey, notice.adm, kind, targets,
            notice.terrakey in previous.all_assignments,
            any(key in previous.all_assignments for key in targets), basis,
        ))
    return changes
