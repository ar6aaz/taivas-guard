"""Deterministic screening rules. A rule flags a possible concern; an expert decides.

No rule here says a filing breaks a regulation. Exceeding a limit needs the pfd engine
(ARCHITECTURE.md section 4.5), which plugs in as another Rule.
"""
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from ..config import Settings
from .m4 import M4Band, load_m4_conflict_bands
from .space import SATELLITE_NOTICE_TYPES, SpaceIssue, SpaceNotice, add_months

FINLAND = "FIN"


@dataclass
class Hit:
    rule_id: str
    family_id: int
    severity: str
    title: str
    adm: str | None
    network: str | None
    notice_ids: list[int]
    details: dict[str, Any]
    comment_deadline: date | None


class Rule(Protocol):
    id: str

    def run(self, issue: SpaceIssue) -> Iterable[Hit]: ...


def _deadline(notices: list[SpaceNotice], months: int) -> date | None:
    published = [n.published for n in notices if n.published]
    return add_months(max(published), months) if published else None


def _families(notices: Iterable[SpaceNotice]) -> dict[int, list[SpaceNotice]]:
    grouped: dict[int, list[SpaceNotice]] = defaultdict(list)
    for notice in notices:
        grouped[notice.family_id].append(notice)
    return grouped


def visible_from_finland(notice: SpaceNotice, s: Settings) -> tuple[bool, str]:
    if notice.ntc_type == "G":
        if notice.gso_lon_deg is None:
            return True, "GSO longitude missing; kept as a precaution"
        visible = s.gso_visible_west_deg <= notice.gso_lon_deg <= s.gso_visible_east_deg
        return visible, f"GSO at {notice.gso_lon_deg} deg, inside the arc visible from Finland"
    if not notice.inclinations_deg:
        return True, "Orbit inclination missing; kept as a precaution"
    # a retrograde orbit reaches the same latitude as its supplement (e.g. 98 deg reaches 82 deg)
    reach = max(min(i, 180 - i) for i in notice.inclinations_deg)
    return reach >= s.ngso_min_inclination_deg, f"Non-GSO reaching {reach:g} deg latitude"


class FinlandNamedRule:
    """Tier 1 in ARCHITECTURE.md 4.4: the BR itself names Finland in a filing's coordination data."""

    id = "fin-named-by-br"

    def __init__(self, settings: Settings):
        self.settings = settings

    def run(self, issue: SpaceIssue) -> Iterable[Hit]:
        named = issue.provisions_for_adm(FINLAND)
        for family_id, notices in _families(issue.notices[i] for i in named).items():
            provisions = sorted({p for n in notices for p in named[n.ntc_id]["provisions"]})
            groups = sorted({g for n in notices for g in named[n.ntc_id]["groups"]})
            head = notices[0]
            yield Hit(
                rule_id=self.id,
                family_id=family_id,
                severity="high",
                title=f"BR names Finland in {head.name or head.ntc_id} ({head.adm}) under {', '.join(provisions) or 'coordination data'}",
                adm=head.adm,
                network=head.name,
                notice_ids=sorted(n.ntc_id for n in notices),
                details={
                    "provisions": provisions,
                    "group_ids": groups,
                    "sections": sorted({n.provision for n in notices if n.provision}),
                    "basis": "Administration FIN appears in the provn or tr_provn tables of this IFIC.",
                },
                comment_deadline=_deadline(notices, self.settings.comment_window_months),
            )


class M4NoDownlinkRule:
    """A satellite visible from Finland transmits in a band M4 gives no space-to-Earth allocation.

    Assumes the satellite reaches Finland when its orbit allows it. Beam coverage is not checked
    yet, so this over-flags by design (ARCHITECTURE.md principle 6: screening is conservative).
    """

    id = "m4-no-space-to-earth"

    def __init__(self, settings: Settings, bands: list[M4Band]):
        self.settings = settings
        self.bands = sorted(bands, key=lambda b: b.low_mhz)

    def run(self, issue: SpaceIssue) -> Iterable[Hit]:
        satellites = (n for n in issue.notices.values() if n.ntc_type in SATELLITE_NOTICE_TYPES)
        for family_id, notices in _families(satellites).items():
            matched: dict[M4Band, set[int]] = defaultdict(set)
            reasons = set()
            for notice in notices:
                visible, why = visible_from_finland(notice, self.settings)
                if not visible:
                    continue
                reasons.add(why)
                for a in notice.assignments:
                    if a.get("emi_rcp") != "E" or a.get("freq_min") is None or a.get("freq_max") is None:
                        continue  # E = the space station transmits
                    for band in self.bands:
                        if float(a["freq_min"]) < band.high_mhz and float(a["freq_max"]) > band.low_mhz:
                            matched[band].add(int(a["grp_id"]))
            if not matched:
                continue
            head = notices[0]
            passive = any(b.passive for b in matched)
            assumptions = ["Beam coverage over Finland is not checked.", "No power or pfd calculation was done."]
            if unresolved := sorted({b.space_down_unresolved for b in matched if b.space_down_unresolved}):
                assumptions.append(
                    f"M4 gives no direction for {'; '.join(unresolved)}; not counted as a space-to-Earth allocation."
                )
            if any(b.no_row_gaps_mhz for b in matched):
                assumptions.append(
                    "Parts of a matched band have no M4 sub-band row (no_row_gaps_mhz); "
                    "its primary terrestrial service is assumed to apply there."
                )
            yield Hit(
                rule_id=self.id,
                family_id=family_id,
                severity="high" if passive else "medium",
                title=(
                    f"{head.name or head.ntc_id} ({head.adm}) transmits in {len(matched)} band(s) "
                    "with no space-to-Earth allocation in Finland"
                    + (", including a passive band" if passive else "")
                ),
                adm=head.adm,
                network=head.name,
                notice_ids=sorted(n.ntc_id for n in notices),
                details={
                    "bands": [
                        {
                            "low_mhz": b.low_mhz,
                            "high_mhz": b.high_mhz,
                            "m4_primary_services": b.primary_services,
                            "finnish_use": b.finnish_use,
                            "passive": b.passive,
                            "m4_table_pages": b.pages,
                            "group_ids": sorted(groups),
                            **({"m4_api_row_ids": b.api_row_ids, "m4_snapshot": b.source} if b.api_row_ids else {}),
                            **({"space_down_unresolved": b.space_down_unresolved} if b.space_down_unresolved else {}),
                            **({"no_row_gaps_mhz": b.no_row_gaps_mhz} if b.no_row_gaps_mhz else {}),
                        }
                        for b, groups in sorted(matched.items(), key=lambda kv: kv[0].low_mhz)
                    ],
                    "visibility": sorted(reasons),
                    "sections": sorted({n.provision for n in notices if n.provision}),
                    "assumptions": assumptions,
                },
                comment_deadline=_deadline(notices, self.settings.comment_window_months),
            )


def space_rules(settings: Settings) -> list[Rule]:
    from .rule_stations import station_overlap_rule  # imported here because rule_stations uses Hit from this module
    from .rule_pfd import pfd_rule

    return [
        FinlandNamedRule(settings),
        M4NoDownlinkRule(settings, load_m4_conflict_bands(settings.m4_filter_path)),
        station_overlap_rule(settings),
        pfd_rule(settings),
    ]
