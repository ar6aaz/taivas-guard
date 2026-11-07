"""The deterministic draft: built from case data alone, with no model and no API key. It is also the fallback."""
from dataclasses import dataclass
from datetime import date
from numbers import Real
from typing import Any

from .retrieval import Passage, provision_number, wanted_provisions

BANNER = "DRAFT FOR EXPERT REVIEW. NOT SENT. Nothing in this system sends letters; an expert decides whether and how to use it."
CALCULATED_METHODS = {"pfd_gso", "pfd_ngso", "p1546"}
_METHOD_NAMES = {
    "pfd_gso": "a power flux-density calculation for a geostationary network",
    "pfd_ngso": "a power flux-density calculation for a non-geostationary system",
    "p1546": "a field-strength calculation using the ITU-R P.1546 propagation model",
}
_SECTION_NAMES = {"9.1": "advance publication", "9.6": "coordination request"}


@dataclass(frozen=True)
class CaseView:
    """The case fields a draft may use, detached from the database session."""

    id: int
    domain: str
    rule_id: str
    severity: str
    title: str
    adm: str | None
    network: str | None
    notice_ids: list[str]
    details: dict[str, Any]
    first_issue: int
    last_issue: int
    comment_deadline: date | None
    source_sha256: str
    review_status: str = "pending"

    @classmethod
    def of(cls, case: Any) -> "CaseView":
        return cls(**{name: getattr(case, name) for name in cls.__dataclass_fields__})

    @property
    def issue_no(self) -> int:
        source = self.details.get("source") or {}
        return source.get("issue_no") or self.last_issue


def _is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def is_calculated(details: dict[str, Any]) -> bool:
    """True only with a known calculation method and all three results. Anything less is screening."""
    return details.get("method") in CALCULATED_METHODS and all(
        _is_number(details.get(key)) for key in ("value_db", "limit_db", "margin_db")
    )


def is_upper_bound(details: dict[str, Any]) -> bool:
    """A non-GSO envelope bounds the margin from above, so a positive one does not show a limit passed."""
    return is_calculated(details) and details.get("margin_basis") == "envelope"


def _db(value: float) -> float:
    """The precision a letter quotes. The case keeps the full value as evidence."""
    return round(float(value), 2)


def margin_of(details: dict[str, Any]) -> float | None:
    """The margin a text may treat as a result. An upper bound's is not one, so it can't support saying a limit is passed."""
    return float(details["margin_db"]) if is_calculated(details) and not is_upper_bound(details) else None


def levels_of(details: dict[str, Any]) -> dict[str, float]:
    if not is_calculated(details):
        return {}
    return {"value": _db(details["value_db"]), "limit": _db(details["limit_db"]), "margin": _db(details["margin_db"])}


def _mhz(value: float) -> str:
    return f"{value:f}".rstrip("0").rstrip(".")


def _band(band: dict[str, Any]) -> str:
    return f"{_mhz(band['low_mhz'])}-{_mhz(band['high_mhz'])} MHz"


def _joined(values: list[str]) -> str:
    return ", ".join(values) if values else "none recorded"


def _section_label(section: str) -> str:
    name = _SECTION_NAMES.get(provision_number(section))
    return f"{section} ({name})" if name else section


def facts(case: CaseView) -> dict[str, Any]:
    """The block printed with every draft. Code renders it, so a model can never alter these values."""
    d = case.details
    out: dict[str, Any] = {
        "case_id": case.id,
        "domain": case.domain,
        "rule": case.rule_id,
        "severity": case.severity,
        "notifying_administration": case.adm,
        "network": case.network,
        "notice_ids": case.notice_ids,
        "ific_issue": case.issue_no,
        "sections": d.get("sections", []),
        "provisions_named_by_br": d.get("provisions", []),
        "comment_deadline_estimate": case.comment_deadline.isoformat() if case.comment_deadline else None,
        "source_file_sha256": case.source_sha256,
        "method": d.get("method", "screening") if is_calculated(d) else "screening",
    }
    if d.get("bands"):
        out["bands"] = [
            {
                "band": _band(b),
                "m4_primary_services": b.get("m4_primary_services"),
                "finnish_use": b.get("finnish_use"),
                "passive": b.get("passive"),
                "m4_table_pages": b.get("m4_table_pages"),
            }
            for b in d["bands"]
        ]
    if d.get("visibility"):
        out["visibility"] = d["visibility"]
    if d.get("station_ids"):
        out["station_ids"] = d["station_ids"]
    if is_calculated(d):
        out["calculation"] = {
            key: d.get(key) for key in ("value_db", "limit_db", "margin_db", "mask_id", "worst_point", "calc_inputs")
        }
    return out


def citations(case: CaseView, passages: list[Passage]) -> list[dict[str, Any]]:
    """Every source a draft relies on: the case's own citations, then the retrieved clauses."""
    out = [dict(c) for c in case.details.get("citations", [])]
    out += [{"doc": p.doc, "clause": p.clause, "page_or_url": p.page_or_url} for p in passages]
    return list({(c.get("doc"), c.get("clause")): c for c in out}.values())


def _concern_paragraphs(case: CaseView) -> list[str]:
    d = case.details
    if case.rule_id == "fin-named-by-br":
        provisions = _joined(d.get("provisions", []))
        return [
            f"The data published by the Radiocommunication Bureau in this IFIC names Finland in connection with "
            f"this filing, under provision(s) {provisions}.",
        ]
    if d.get("bands"):
        lines = [
            f"- {_band(b)}: M4 primary services {b.get('m4_primary_services') or 'not listed'}; "
            f"Finnish use: {b.get('finnish_use') or 'not listed'}; M4 Frequency Allocation Table page(s) {b.get('m4_table_pages')}"
            + ("; passive or radio astronomy band" if b.get("passive") else "")
            for b in d["bands"]
        ]
        return [
            "According to the filing, the space station transmits in the band(s) below, for which the Finnish "
            "Frequency Allocation Table (Regulation M4) gives no space-to-Earth allocation:",
            "\n".join(lines),
        ]
    return [f"The screening rule {case.rule_id} raised this filing for review: {case.title}."]


def _position_paragraphs(case: CaseView) -> list[str]:
    d = case.details
    if not is_calculated(d):
        return [
            "This is a possible concern from screening. No pfd or field-strength calculation has been made, and "
            "this letter makes no finding on compliance with any limit or provision.",
            "Finland asks for the information needed to assess the concern, in particular the transmitting beams "
            "and any coverage over Finnish territory.",
        ]
    method = _METHOD_NAMES[d["method"]]
    mask = f" (mask {d['mask_id']})" if d.get("mask_id") else ""
    if is_upper_bound(d):
        bound = (
            f"Under the assumptions stated below, {method} gives an upper bound of {_db(d['value_db'])} dB against a limit of "
            f"{_db(d['limit_db'])} dB{mask}, a margin of {_db(d['margin_db'])} dB. An upper bound may describe a state the satellite "
            "never occupies, so it establishes neither an exceedance nor compliance. It is raised here for expert review."
        )
        return [bound, "Finland asks for the information needed to confirm or correct these assumptions."]
    numbers = f"a calculated value of {_db(d['value_db'])} dB against a limit of {_db(d['limit_db'])} dB{mask}, a margin of {_db(d['margin_db'])} dB"
    if d["margin_db"] > 0:
        outcome = "the calculated value exceeds the limit. This result is subject to expert review."
    else:
        position = "at" if d["margin_db"] == 0 else "below"
        outcome = f"the calculated value is {position} the limit. It is raised here for expert review."
    result = f"Under the assumptions stated below, {method} gives {numbers}. On those assumptions {outcome}"
    return [result, "Finland asks for the information needed to confirm or correct these assumptions."]


def draft_letter_body(case: CaseView) -> str:
    network = case.network or "the network"
    sections = [_section_label(s) for s in case.details.get("sections", [])]
    opening = (
        f"The Administration of Finland has reviewed BR IFIC {case.issue_no}, which publishes {network} "
        f"(notifying administration {case.adm or 'not recorded'}; notice ID(s) {_joined(case.notice_ids)}"
        + (f"; section(s) {_joined(sections)}" if sections else "")
        + ")."
    )
    parts = [opening, *_concern_paragraphs(case), *_position_paragraphs(case)]
    if case.comment_deadline:
        parts.append(f"Finland notes the estimated comment deadline of {case.comment_deadline.isoformat()}.")
    parts.append(
        "[Expert to complete: the assignments Finland relies on, Finland's position, and any suggestion for resolving the matter.]"
    )
    return "\n\n".join(parts)


def explain_case(case: CaseView) -> str:
    d = case.details
    network = case.network or "This filing"
    if is_calculated(d):
        outcome = (
            f"The upper bound gives a margin of {_db(d['margin_db'])} dB against the limit, under the listed assumptions. "
            "It does not show that a limit is exceeded."
            if is_upper_bound(d)
            else f"The calculation gives a margin of {_db(d['margin_db'])} dB against the limit, under the listed assumptions."
        )
    else:
        outcome = "No calculation has been made, so this is a possible concern and not a finding about any limit."
    if case.rule_id == "fin-named-by-br":
        why = f"The Bureau's own data for {network} names Finland under {_joined(d.get('provisions', []))}."
    elif d.get("bands"):
        bands = _joined([_band(b) for b in d["bands"]])
        why = (
            f"{network} transmits in {bands}, where the Finnish table (Regulation M4) gives no space-to-Earth "
            "allocation, and its orbit can reach Finland."
        )
    else:
        why = f"Rule {case.rule_id} raised it: {case.title}."
    return f"{why} {outcome} An expert decides whether Finland responds."


def review_notes(case: CaseView, passages: list[Passage], missing: list[str]) -> list[str]:
    """Points for the reviewing expert. They are not part of the letter."""
    d = case.details
    notes = [str(a) for a in d.get("assumptions", [])]
    have = {p.provision for p in passages}
    if case.comment_deadline:
        notes.append("The deadline is an estimate from the IFIC date. Check the date printed in the Special Section.")
    if "9.52C" in have:
        notes.append(
            "Under No. 9.52C, an administration not responding within the period is regarded as unaffected. Decide before the deadline."
        )
    if "9.52" in have:
        notes.append("No. 9.52 asks a disagreeing administration for its own assignments. This draft attaches none.")
    if "9.52A" in have:
        notes.append("No. 9.52A lets an administration ask the Bureau to apply the Appendix 5 criteria.")
    if "9.3" in have and d.get("bands"):
        notes.append(
            "No. 9.3 concerns existing or planned satellite networks or systems. Decide whether this concern, "
            "based on terrestrial use in M4, fits that route."
        )
    if case.rule_id != "fin-named-by-br" and d.get("bands"):
        notes.append("This rule does not check whether the Bureau also names Finland for this filing.")
    if not is_calculated(d) and d.get("method") in CALCULATED_METHODS:
        notes.append("The case names a calculation method but lacks its results, so it is treated as screening.")
    if missing:
        notes.append(f"Not in the reference index, so cited from case data only: {_joined(missing)}.")
    notes.append("Addressee, signatory and transmission route are left for the expert.")
    return notes


def reference_block(case: CaseView, cited: list[dict[str, Any]]) -> str:
    """The values the letter rests on, rendered by code so that no rewrite can change them."""
    d = case.details
    lines = [
        f"Network: {case.network or 'not named'}",
        f"Notifying administration: {case.adm or 'not recorded'}",
        f"Notice ID(s): {_joined(case.notice_ids)}",
        f"BR IFIC: {case.issue_no}",
    ]
    if d.get("sections"):
        lines.append(f"Section(s): {_joined([_section_label(s) for s in d['sections']])}")
    if d.get("provisions"):
        lines.append(f"Provision(s) named by the Bureau: {_joined(d['provisions'])}")
    if d.get("bands"):
        lines.append(f"Band(s): {_joined([_band(b) for b in d['bands']])}")
    if case.comment_deadline:
        lines.append(f"Estimated comment deadline: {case.comment_deadline.isoformat()}")
    if is_calculated(d):
        lines.append(f"Method: {d['method']}")
        lines.append(f"Calculated value: {_db(d['value_db'])} dB; limit: {_db(d['limit_db'])} dB; margin: {_db(d['margin_db'])} dB")
        if d.get("mask_id"):
            lines.append(f"Mask: {d['mask_id']}")
        if point := d.get("worst_point"):
            lines.append(f"Worst point: {', '.join(f'{k} {v}' for k, v in point.items())}")
    else:
        lines.append("Method: screening only; no calculation made")
    if d.get("assumptions"):
        lines.append("Assumptions: " + " ".join(str(a) for a in d["assumptions"]))
    if cited:
        lines.append("References:\n" + "\n".join(f"- {c.get('doc')}: {c.get('clause')}" for c in cited))
    return "\n".join(lines)


def evidence_values(case: CaseView, passages: list[Passage]) -> list[Any]:
    """Case data and passages as guardrail evidence. Provisions are added separately, by number."""
    return [
        facts(case),
        case.details,
        list(levels_of(case.details).values()),
        case.title,
        ["Regulation M4, Frequency Allocation Table"] if case.details.get("bands") else [],
        [f"Article {provision_number(p).split('.')[0]}" for p in wanted_provisions(case.details)],
        [[p.doc, p.clause, p.text] for p in passages],
    ]
