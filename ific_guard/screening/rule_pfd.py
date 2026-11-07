"""PFD rules for notified space filings.

Every result says what kind of result it is. For GSO, a calculated margin over the Finnish points.
For non-GSO, either a margin seen in a one-minute propagation of the notified orbit, or an upper
bound over every geometry the orbit could reach (see `engines.pfd_ngso`). A bound above the limit
is not reported as exceeding it.

Three outcomes without a number are kept apart: not visible from Finland is a computed result;
missing inputs need review; inconsistent inputs are data-quality items. GIMS contours are not
available, so notified maximum gain is applied towards Finland: "exceeds" always means under
worst-case assumptions, and being below a limit is never a compliance finding.
"""
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ..config import Settings
from ..engines.pfd import GroundPoint, LimitMask, evaluate_gso, finland_points, load_masks
from ..engines.pfd_ngso import (
    ENVELOPE_STEP_DEG,
    PROPAGATION_MINUTES,
    PROPAGATION_STEP_MINUTES,
    best_sample,
    envelope,
    propagate,
)
from .rules import Hit, Rule, _deadline, _families
from .scoring import ScoringWeights, score_finding
from .space import SATELLITE_NOTICE_TYPES, SpaceIssue, SpaceNotice

WATCH_DB = 3.0  # ARCHITECTURE.md 4.7: the watch list is within about 3 dB below the limit
MIN_ROWS_FOR_STATISTICS = 1000
EARTH_BODY = "T"

POWER_DENSITY_CITATION = {
    "doc": "IFIC Space Preface (secondary schema note; primary copy unavailable)",
    "clause": "emiss table, pwr_ds_max",
    "page_or_url": "docs/research-discarded/itu-primary-sources.md, p. 85",
}
UNIT_ASSUMPTION = "pwr_ds_max is treated as dBW/Hz per a secondary schema note; primary Space Preface confirmation is unavailable."
OPEN_ITEMS = ["needs-info: the pwr_ds_max unit rests on a secondary note; the primary ITU Space Preface could not be retrieved."]
PEAK_CAP_ASSUMPTION = (
    "Power in the reference bandwidth is pwr_ds_max plus 10 log10(reference bandwidth), capped by the row's pep_max, treated as dBW; "
    "the emission row used is the one with the most power after that cap. A row without pep_max is not capped."
)
ENVELOPE_ASSUMPTIONS = [
    "The envelope pairs each arrival angle with the shortest distance the orbit can have at that angle, for any node, phase, epoch or argument of perigee, at altitudes from notified perigee to apogee.",
    "Latitude reach comes from inclination and the southernmost Finnish point; relative longitude is unconstrained.",
    "Where orbit_lnk names the orbit rows a beam flies on, only those rows are used. This reading of orbit_lnk is not confirmed against the primary Space Preface.",
    f"The envelope is sampled every {ENVELOPE_STEP_DEG} degrees of arrival angle, including every mask breakpoint.",
    "The envelope does not cover station-keeping excursions beyond the notified altitudes, or more than one satellite adding up at a point. It may describe states the satellite never occupies.",
]
PROPAGATION_ASSUMPTIONS = [
    "Propagation is two-body with no J2, over one day in one-minute steps, for one satellite per distinct orbit shape, so it can miss the worst case and is not an upper bound.",
    "No epoch is notified: at minute 0 the node lies at the notified long_asc (or right_asc if that is absent) as an Earth-fixed longitude, and the satellite is phase_ang past the node. These field meanings are not confirmed against the primary Space Preface.",
]


def _number(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if value is not None and value != "" else None


@lru_cache(maxsize=None)
def _select(low: float, high: float, kind: str, orbit_type: str, inclination: float | None, apogee: float | None):
    return tuple(load_masks().select(low, high, kind, orbit_type, inclination, apogee))


def _citation(mask: LimitMask) -> dict[str, Any]:
    return {"doc": mask.citation.get("doc", "RR"), **mask.citation}


def _result(margin: float) -> str:
    return "exceeds" if margin > 0 else "within_3db" if margin >= -WATCH_DB else "below"


@dataclass(frozen=True)
class PowerScreen:
    """Finds implausible notified power density without using any regulatory value.

    `inconsistent` is a contradiction inside one row: a density per hertz above the row's own total
    power. That row is not used. `fence` is a far-out value (Tukey, Q3 + 3 x IQR) of this issue's own
    transmit rows. It is a statistical flag only, so those rows are still evaluated.
    """
    q1: float | None
    q3: float | None
    fence: float | None

    @classmethod
    def of(cls, issue: SpaceIssue) -> "PowerScreen":
        values = sorted(
            power
            for group_id, rows in issue.emissions.items()
            if issue.groups.get(group_id, {}).get("emi_rcp") == "E" and issue.group_notice.get(group_id) in issue.notices
            for row in rows
            if (power := _number(row, "pwr_ds_max")) is not None and not cls.inconsistent(row)
        )
        if len(values) < MIN_ROWS_FOR_STATISTICS:
            return cls(None, None, None)
        q1, q3 = values[len(values) // 4], values[3 * len(values) // 4]
        return cls(q1, q3, q3 + 3 * (q3 - q1))

    @staticmethod
    def inconsistent(row: dict[str, Any]) -> bool:
        power, peak = _number(row, "pwr_ds_max"), _number(row, "pep_max")
        return power is not None and peak is not None and power > peak


def _best_join(issue: SpaceIssue, assignment: dict[str, Any]) -> tuple[float | None, float | None, dict[str, Any], list[dict[str, Any]]]:
    """Maximum usable power density and gain, the join details, and any rows set aside as inconsistent."""
    group_id = int(assignment["grp_id"])
    group = issue.groups.get(group_id, {})
    rows = issue.emissions.get(group_id, [])
    excluded = [
        {
            "group_id": group_id,
            "seq_no": row.get("seq_no"),
            "pwr_ds_max": _number(row, "pwr_ds_max"),
            "pep_max": _number(row, "pep_max"),
            "design_emi": row.get("design_emi"),
            "check": "density_above_total_power",
            "action": "row not used",
        }
        for row in rows
        if PowerScreen.inconsistent(row)
    ]
    usable = [row for row in rows if _number(row, "pwr_ds_max") is not None and not PowerScreen.inconsistent(row)]
    chosen = max(usable, key=lambda row: _number(row, "pwr_ds_max")) if usable else None
    beam_key = (
        int(assignment["ntc_id"]),
        assignment.get("emi_rcp") or group.get("emi_rcp"),
        assignment.get("beam_name") or group.get("beam_name"),
    )
    gains = [g for g in (_number(row, "gain") for row in issue.beams.get(beam_key, [])) if g is not None]
    return (
        _number(chosen, "pwr_ds_max") if chosen else None,
        max(gains) if gains else None,
        {
            "group_id": group_id,
            "beam_name": assignment.get("beam_name") or group.get("beam_name"),
            "emission_seq_no": chosen.get("seq_no") if chosen else None,
            "pep_max_dbw": _number(chosen, "pep_max") if chosen else None,
            "group": group,
        },
        excluded,
    )


def _power_in_ref_bw(power: float, peak: float | None, ref_bw_hz: float) -> float:
    """The same cap `engines.pfd.pfd_db` applies: an emission can't put more than its total power in the bandwidth."""
    in_ref = power + 10 * math.log10(ref_bw_hz)
    return in_ref if peak is None else min(in_ref, peak)


def _dedupe_power(power: float | None, peak: float | None, mask: LimitMask) -> float | None:
    """Records with the same power in the reference bandwidth give the same result, so they count once."""
    return None if power is None else _power_in_ref_bw(power, peak, mask.ref_bw_hz)


def _emission_for_mask(
    issue: SpaceIssue, join: dict[str, Any], mask: LimitMask
) -> tuple[float | None, float | None, dict[str, Any]]:
    """The usable row with the most power in this mask's bandwidth. Capping can make it differ from the densest row."""
    rows = [
        row for row in issue.emissions.get(join["group_id"], [])
        if _number(row, "pwr_ds_max") is not None and not PowerScreen.inconsistent(row)
    ]
    if not rows:
        return None, None, join
    chosen = max(rows, key=lambda row: _power_in_ref_bw(_number(row, "pwr_ds_max"), _number(row, "pep_max"), mask.ref_bw_hz))
    peak = _number(chosen, "pep_max")
    return _number(chosen, "pwr_ds_max"), peak, {**join, "emission_seq_no": chosen.get("seq_no"), "pep_max_dbw": peak}


def _flags(power: float, screen: PowerScreen, join: dict[str, Any], ntc_id: int) -> list[dict[str, Any]]:
    if screen.fence is None or power <= screen.fence:
        return []
    return [{
        "ntc_id": ntc_id,
        "group_id": join["group_id"],
        "seq_no": join["emission_seq_no"],
        "pwr_ds_max": power,
        "pep_max": join["pep_max_dbw"],
        "check": "power_density_statistical_outlier",
        "fence_dbw_hz": screen.fence,
        "action": "evaluated, flagged, margin not scored",
    }]


def _gso_calculation(
    notice: SpaceNotice, power: float, peak: float | None, gain: float, join: dict[str, Any], assignment: dict[str, Any],
    mask: LimitMask, points: tuple[GroundPoint, ...],
) -> dict[str, Any] | None:
    result = evaluate_gso(notice.gso_lon_deg, power, gain, mask, points, peak_power_dbw=peak)
    if result.worst is None:
        return None
    return {
        "method": "pfd_gso",
        "result": _result(result.worst.margin_db),
        "margin_basis": "gso_grid",
        "ntc_id": notice.ntc_id,
        "calc_inputs": {
            **result.calc_inputs,
            "f_low_mhz": float(assignment["freq_min"]),
            "f_high_mhz": float(assignment["freq_max"]),
            "bandwidth_hz": _number(assignment, "bdwdth"),
            "ref_bw_hz": mask.ref_bw_hz,
            "power_in_ref_bw_dbw": _power_in_ref_bw(power, peak, mask.ref_bw_hz),
            "points_evaluated": result.points_evaluated,
            **join,
        },
        "assumptions": [
            *result.assumptions,
            UNIT_ASSUMPTION,
            PEAK_CAP_ASSUMPTION,
            "Maximum gain and power density are used for the whole assignment.",
        ],
        "worst_point": {
            "lat": result.worst.lat_deg,
            "lon": result.worst.lon_deg,
            "angle_deg": result.worst.angle_deg,
            "distance_km": result.worst.distance_km,
        },
        "value_db": result.worst.value_db,
        "limit_db": result.worst.limit_db,
        "margin_db": result.worst.margin_db,
        "mask_id": result.mask_id,
        "citations": [_citation(mask), POWER_DENSITY_CITATION],
        "band": {"low_mhz": float(assignment["freq_min"]), "high_mhz": float(assignment["freq_max"])},
    }


@dataclass(frozen=True)
class OrbitShape:
    """One distinct perigee, apogee and inclination in a notice, with the first row that can be flown."""
    perigee_km: float
    apogee_km: float
    inclination_deg: float
    orbit_rows: int
    satellites: int
    orb_id: int | None = None
    perigee_arg_deg: float | None = None
    node_lon_deg: float | None = None
    node_field: str | None = None
    phase_deg: float | None = None
    not_flown_reason: str | None = None


def _orbit_shapes(issue: SpaceIssue, ntc_id: int, rows: list[dict[str, Any]]) -> tuple[list[OrbitShape], int]:
    """Distinct shapes in orbit-row order, and how many rows lacked an element needed for any bound."""
    grouped: dict[tuple[float, float, float], list[dict[str, Any]]] = {}
    incomplete = 0
    for row in rows:
        perigee, apogee, inclination = _number(row, "perig_km"), _number(row, "apog_km"), _number(row, "inclin_ang")
        if perigee is None or apogee is None or inclination is None:
            incomplete += 1
            continue
        grouped.setdefault((perigee, apogee, inclination), []).append(row)
    shapes = []
    for (perigee, apogee, inclination), members in grouped.items():
        common = dict(
            perigee_km=perigee, apogee_km=apogee, inclination_deg=inclination, orbit_rows=len(members),
            satellites=sum(int(m.get("nbr_sat_pl") or 0) for m in members),
        )
        reason = "no orbit row has the node, phase and (if eccentric) argument of perigee together"
        for member in members:
            orb_id = int(member["orb_id"]) if member.get("orb_id") is not None else None
            perigee_arg = _number(member, "perig_arg")
            if perigee_arg is None and apogee != perigee:
                continue
            field = "long_asc" if _number(member, "long_asc") is not None else "right_asc"
            node, phase = _number(member, field), issue.first_phases.get((ntc_id, orb_id))
            if node is None or phase is None:
                continue
            shapes.append(OrbitShape(
                **common, orb_id=orb_id, perigee_arg_deg=perigee_arg or 0.0, node_lon_deg=node, node_field=field,
                phase_deg=phase,
            ))
            break
        else:
            shapes.append(OrbitShape(**common, not_flown_reason=reason))
    return shapes, incomplete


def _ngso_calculation(
    notice: SpaceNotice,
    shapes: list[OrbitShape],
    power: float,
    peak: float | None,
    gain: float,
    join: dict[str, Any],
    assignment: dict[str, Any],
    mask: LimitMask,
    points: tuple[GroundPoint, ...],
    south_lat_deg: float,
) -> dict[str, Any] | None:
    """Returns None when no shape can see Finland: the caller records that as a computed result."""
    offset = _power_in_ref_bw(power, peak, mask.ref_bw_hz) + gain
    bound: tuple[float, OrbitShape, Any] | None = None
    reachable: list[OrbitShape] = []
    for shape in shapes:
        best = envelope(shape.perigee_km, shape.apogee_km, shape.inclination_deg, mask, south_lat_deg)
        if best is None:
            continue
        reachable.append(shape)
        if bound is None or best.relative_margin_db > bound[0]:
            bound = (best.relative_margin_db, shape, best)
    if bound is None:
        return None
    envelope_margin = offset + bound[0]

    sampled: tuple[float, OrbitShape, Any, float] | None = None
    flown, minutes_visible = 0, 0
    for shape in reachable:
        if shape.not_flown_reason:
            continue
        flight = propagate(
            shape.perigee_km, shape.apogee_km, shape.inclination_deg, shape.perigee_arg_deg, shape.node_lon_deg,
            shape.phase_deg, points,
        )
        flown += 1
        minutes_visible = max(minutes_visible, flight.minutes_with_finland_visible)
        if (found := best_sample(flight, mask)) and (sampled is None or found[2] > sampled[0]):
            sampled = (found[2], shape, found[0], found[1])
    sampled_margin = offset + sampled[0] if sampled else None

    envelope_block = {
        "kind": "upper_bound",
        "margin_db": envelope_margin,
        "value_db": envelope_margin + bound[2].limit_db,
        "limit_db": bound[2].limit_db,
        "angle_deg": bound[2].angle_deg,
        "distance_km": bound[2].distance_km,
        "altitude_km": bound[2].altitude_km,
        "central_angle_min_deg": bound[2].central_angle_min_deg,
        "from_orbit": _shape_dict(bound[1]),
    }
    propagation_block: dict[str, Any] = {
        "kind": "sample_not_a_bound",
        "minutes": PROPAGATION_MINUTES,
        "step_minutes": PROPAGATION_STEP_MINUTES,
        "shapes_flown": flown,
        "shapes_not_flown": len(reachable) - flown,
        "satellites_flown": flown,
        "points_evaluated": len(points),
        "most_minutes_finland_visible": minutes_visible,
        "margin_db": sampled_margin,
    }
    if flown == 0:
        propagation_block = {
            "kind": "not_run",
            "reason": next((s.not_flown_reason for s in reachable if s.not_flown_reason), None),
        }
    elif sampled:
        propagation_block.update(
            orbit=_shape_dict(sampled[1]), orb_id=sampled[1].orb_id, perigee_arg_deg=sampled[1].perigee_arg_deg,
            node_lon_deg=sampled[1].node_lon_deg, node_field=sampled[1].node_field, phase_deg=sampled[1].phase_deg,
            worst_sample={
                "lat": sampled[2].lat_deg, "lon": sampled[2].lon_deg, "angle_deg": sampled[2].angle_deg,
                "distance_km": sampled[2].distance_km, "altitude_km": sampled[2].altitude_km, "minute": sampled[2].minute,
                "limit_db": sampled[3],
            },
        )

    if sampled_margin is not None and sampled_margin > 0:
        result, basis = "exceeds", "propagated_sample"
        sample, limit = sampled[2], sampled[3]
        value, margin = sampled_margin + limit, sampled_margin
        worst_point = {
            "lat": sample.lat_deg, "lon": sample.lon_deg, "angle_deg": sample.angle_deg,
            "distance_km": sample.distance_km, "altitude_km": sample.altitude_km, "minute": sample.minute,
        }
    else:
        basis = "envelope"
        result = {"exceeds": "envelope_exceeds", "within_3db": "envelope_within_3db", "below": "envelope_below"}[_result(envelope_margin)]
        value, limit, margin = envelope_block["value_db"], envelope_block["limit_db"], envelope_margin
        worst_point = {
            "angle_deg": bound[2].angle_deg,
            "distance_km": bound[2].distance_km,
            "altitude_km": bound[2].altitude_km,
            "located": False,  # a bound over all longitudes has no place on the map
        }

    return {
        "method": "pfd_ngso",
        "result": result,
        "margin_basis": basis,
        "ntc_id": notice.ntc_id,
        "calc_inputs": {
            "f_low_mhz": float(assignment["freq_min"]),
            "f_high_mhz": float(assignment["freq_max"]),
            "p_density_dbw_hz": power,
            "gain_dbi": gain,
            "ref_bw_hz": mask.ref_bw_hz,
            "peak_power_dbw": peak,
            "power_in_ref_bw_dbw": _power_in_ref_bw(power, peak, mask.ref_bw_hz),
            "orbit_shapes": [_shape_dict(s) for s in shapes],
            "orbit_shapes_never_seeing_finland": len(shapes) - len(reachable),
            "orbit_rows_considered": sum(s.orbit_rows for s in shapes),
            "satellites_notified": sum(s.satellites for s in shapes),
            "finland_south_lat_deg": south_lat_deg,
            **join,
        },
        "envelope": envelope_block,
        "propagation": propagation_block,
        "assumptions": [
            *ENVELOPE_ASSUMPTIONS,
            *PROPAGATION_ASSUMPTIONS,
            "Free-space propagation (RR No. 21.16).",
            "Angle of arrival is geometric elevation on a spherical Earth, with no refraction.",
            "Maximum notified gain applies towards every point at every moment. With boresight gain this over-estimates pfd off-axis.",
            UNIT_ASSUMPTION,
            PEAK_CAP_ASSUMPTION,
        ],
        "worst_point": worst_point,
        "value_db": value,
        "limit_db": limit,
        "margin_db": margin,
        "mask_id": mask.mask_id,
        "citations": [_citation(mask), POWER_DENSITY_CITATION],
        "band": {"low_mhz": float(assignment["freq_min"]), "high_mhz": float(assignment["freq_max"])},
    }


def _shape_dict(shape: OrbitShape) -> dict[str, Any]:
    return {
        "perigee_km": shape.perigee_km,
        "apogee_km": shape.apogee_km,
        "inclination_deg": shape.inclination_deg,
        "orbit_rows": shape.orbit_rows,
    }


CONFIRMED = ("exceeds",)
TIER = {"exceeds": 5, "envelope_exceeds": 4, "within_3db": 3, "envelope_within_3db": 3, "below": 1, "envelope_below": 1}


def _rank(calculation: dict[str, Any]) -> tuple[int, int, float]:
    trusted = 0 if calculation.get("data_quality_flags") else 1
    return trusted, TIER[calculation["result"]], calculation["margin_db"]


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _headline(worst: dict[str, Any]) -> str:
    result = worst["result"]
    flagged = " (input flagged as a data-quality item)" if worst.get("data_quality_flags") else ""
    if result == "exceeds":
        how = " in a one-day propagation" if worst["method"] == "pfd_ngso" else ""
        return f" exceeds{how} under worst-case assumptions{flagged}"
    if result == "envelope_exceeds":
        shown = "exceedance not shown by propagation" if worst["propagation"]["kind"] != "not_run" else "no time-sampled result"
        return f": upper bound above the limit; {shown}{flagged}"
    if result == "within_3db":
        return f" within 3 dB below the limit under worst-case assumptions; watch list{flagged}"
    if result == "envelope_within_3db":
        return f": upper bound within 3 dB below the limit; not a compliance finding{flagged}"
    if result == "envelope_below":
        return f": upper bound below the limit for this orbit and these inputs; not a compliance finding; expert review required{flagged}"
    return f" below the limit at the evaluated points; not a compliance finding; expert review required{flagged}"


def _hit(
    rule_id: str,
    family_id: int,
    notices: list[SpaceNotice],
    calculations: list[dict[str, Any]],
    missing: list[dict[str, Any]],
    not_visible: list[dict[str, Any]],
    data_quality: list[dict[str, Any]],
    settings: Settings,
) -> Hit:
    worst = max(calculations, key=_rank) if calculations else None
    reasons = Counter(row["reason"] for row in missing)
    details: dict[str, Any] = {
        "method": worst["method"] if worst else "screening",
        "calculations": calculations,
        "missing": missing,
        "unevaluated_count": len(missing),
        "unevaluated_by_reason": dict(sorted(reasons.items())),
        "not_visible": not_visible,
        "not_visible_count": len(not_visible),
        "data_quality": data_quality,
        "data_quality_count": len(data_quality),
        "concern": bool(calculations or missing or data_quality),
        "expert_review": True,
        "open_items": OPEN_ITEMS,
        "assumptions": [
            "Results are possible concerns for expert review; they do not establish compliance.",
            "Records with missing inputs are counted for expert review. Not visible from Finland is a computed result, counted separately.",
            "Mask rows are chosen by band and, for non-GSO, orbit shape, not by the filing's service.",
        ],
    }
    flagged = bool(worst and worst.get("data_quality_flags"))
    if worst:
        details.update({key: worst[key] for key in ("calc_inputs", "worst_point", "value_db", "limit_db", "margin_db", "mask_id", "citations", "result", "margin_basis")})
        details["source_rows"] = {"notice_ids": [n.ntc_id for n in notices]}
        details["input_flagged"] = flagged
        details["result_counts"] = dict(sorted(Counter(c["result"] for c in calculations).items()))
    ranking = score_finding(details, ScoringWeights.from_settings(settings))
    details["severity_score"] = ranking["score"]
    details["severity_reasons"] = ranking["reasons"]

    if worst and worst["result"] in CONFIRMED and not flagged:
        severity = "high"
    elif (worst and worst["result"] not in ("below", "envelope_below")) or missing or data_quality:
        severity = "medium"
    else:
        severity = "low"

    head = notices[0]
    label = "Article 21 limit" if "art21" in rule_id else "Appendix 5 coordination trigger"
    orbit_label = "GSO" if rule_id.startswith("pfd-gso-") else "NGSO"
    title = f"{head.name or head.ntc_id} ({head.adm}) {orbit_label} {label}"
    if worst:
        title += _headline(worst)
    elif missing:
        names = sorted({name for row in missing for name in row["missing"]})
        title += f" not evaluated; missing {', '.join(names)}"
    elif data_quality:
        title += " not evaluated; every notified power density row is inconsistent (data-quality item)"
    else:
        title += " not visible from Finland (computed result; no pfd concern raised)"
    if worst and missing:
        title += f"; {_plural(len(missing), 'record')} not evaluated"
    if (worst or missing or data_quality) and not_visible:
        title += f"; {_plural(len(not_visible), 'record')} not visible from Finland"
    if (worst or missing) and data_quality:
        title += f"; {_plural(len(data_quality), 'data-quality item')}"
    return Hit(rule_id, family_id, severity, title, head.adm, head.name, sorted(n.ntc_id for n in notices), details, _deadline(notices, settings.comment_window_months))


class PfdRule:
    id = "pfd"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.points: tuple[GroundPoint, ...] | None = None
        self._visible: dict[float, bool] = {}

    def run(self, issue: SpaceIssue) -> Iterable[Hit]:
        screen = PowerScreen.of(issue)
        shapes_by_key: dict[tuple[int, str | None], tuple[list[OrbitShape], int]] = {}
        grouped = _families(n for n in issue.notices.values() if n.ntc_type in SATELLITE_NOTICE_TYPES)
        for family_id, family in grouped.items():
            for orbit_type, orbit_prefix, code in (("GSO", "gso", "G"), ("NGSO", "ngso", "N")):
                notices = [n for n in family if n.ntc_type == code]
                if not notices:
                    continue
                for kind, kind_prefix in (("pfd_art21", "art21"), ("pfd_app5", "app5")):
                    calculations: list[dict[str, Any]] = []
                    missing: list[dict[str, Any]] = []
                    not_visible: list[dict[str, Any]] = []
                    quality: dict[tuple[Any, ...], dict[str, Any]] = {}
                    seen: set[tuple[Any, ...]] = set()
                    for notice in notices:
                        if orbit_type == "GSO":
                            self._gso_notice(issue, notice, kind, screen, calculations, missing, not_visible, quality, seen)
                        else:
                            self._ngso_notice(issue, notice, kind, screen, calculations, missing, not_visible, quality, seen, shapes_by_key)
                    if calculations or missing or not_visible or quality:
                        yield _hit(
                            f"pfd-{orbit_prefix}-{kind_prefix}", family_id, notices, calculations, missing, not_visible,
                            list(quality.values()), self.settings,
                        )

    def _gso_notice(self, issue, notice, kind, screen, calculations, missing, not_visible, quality, seen) -> None:
        for assignment in _downlinks(notice):
            low, high = float(assignment["freq_min"]), float(assignment["freq_max"])
            masks = _select(low, high, kind, "GSO", None, None)
            if not masks:
                continue
            power, gain, join, excluded = _best_join(issue, assignment)
            _note_excluded(quality, notice.ntc_id, excluded)
            for mask in masks:
                if notice.gso_lon_deg is None:
                    missing.append({
                        "ntc_id": notice.ntc_id, "group_id": join["group_id"], "mask_id": mask.mask_id,
                        "missing": ["gso_longitude"], "reason": "gso_longitude_missing",
                    })
                    continue
                power, peak, mask_join = _emission_for_mask(issue, join, mask)
                key = (notice.ntc_id, low, high, _dedupe_power(power, peak, mask), gain, mask.mask_id)
                if key in seen:
                    continue
                seen.add(key)
                # Visibility needs neither power nor gain, so it is decided first.
                if not self._gso_visible(notice.gso_lon_deg, mask):
                    not_visible.append({
                        "ntc_id": notice.ntc_id, "group_id": join["group_id"], "mask_id": mask.mask_id,
                        "reason": "gso_below_horizon_from_every_finnish_point", "gso_lon_deg": notice.gso_lon_deg,
                    })
                    continue
                if not _have_inputs(power, gain, mask_join, notice, low, high, mask, missing):
                    continue
                calculation = _gso_calculation(notice, power, peak, gain, mask_join, assignment, mask, self._points())
                _add(calculation, power, screen, mask_join, notice, calculations, quality)

    def _ngso_notice(self, issue, notice, kind, screen, calculations, missing, not_visible, quality, seen, shapes_by_key) -> None:
        rows = issue.orbits.get(notice.ntc_id, [])
        for assignment in _downlinks(notice):
            low, high = float(assignment["freq_min"]), float(assignment["freq_max"])
            power, gain, join, excluded = _best_join(issue, assignment)
            if notice.ref_body not in (None, EARTH_BODY) or not rows:
                # Without an orbit there is no reach to test, so every candidate mask stays in the screen.
                masks = _select(low, high, kind, "NGSO", None, None)
                if masks:
                    _note_excluded(quality, notice.ntc_id, excluded)
                    not_earth = notice.ref_body not in (None, EARTH_BODY)
                    missing.append({
                        "ntc_id": notice.ntc_id,
                        "group_id": join["group_id"],
                        "missing": ["earth_orbit"] if not_earth else ["orbit"],
                        "reason": "orbit_not_earth_centred" if not_earth else "orbit_missing",
                        "ref_body": notice.ref_body,
                        "frequency_screen_only": True,
                    })
                continue
            beam = join["beam_name"]
            cache_key = (notice.ntc_id, beam)
            if cache_key not in shapes_by_key:
                links = issue.orbit_links.get((notice.ntc_id, "E", beam))
                linked = [r for r in rows if r.get("orb_id") is None or not links or int(r["orb_id"]) in links]
                shapes_by_key[cache_key] = _orbit_shapes(issue, notice.ntc_id, linked or rows)
            shapes, incomplete = shapes_by_key[cache_key]
            by_mask: dict[str, tuple[LimitMask, list[OrbitShape]]] = {}
            if shapes:
                for shape in shapes:
                    for mask in _select(low, high, kind, "NGSO", shape.inclination_deg, shape.apogee_km):
                        by_mask.setdefault(mask.mask_id, (mask, []))[1].append(shape)
            elif masks := _select(low, high, kind, "NGSO", None, None):
                _note_excluded(quality, notice.ntc_id, excluded)
                missing.append({
                    "ntc_id": notice.ntc_id, "group_id": join["group_id"], "missing": ["orbit_elements"],
                    "reason": "orbit_elements_incomplete", "masks": [m.mask_id for m in masks], "frequency_screen_only": True,
                })
                continue
            if not by_mask:
                continue
            _note_excluded(quality, notice.ntc_id, excluded)
            for mask, mask_shapes in by_mask.values():
                if mask.parameter is not None:
                    missing.append({
                        "ntc_id": notice.ntc_id,
                        "group_id": join["group_id"],
                        "missing": [mask.parameter["name"]],
                        "reason": "mask_parameter_not_evaluated",
                        "mask_id": mask.mask_id,
                        "frequency_screen_only": True,
                    })
                    continue
                power, peak, mask_join = _emission_for_mask(issue, join, mask)
                key = (notice.ntc_id, low, high, _dedupe_power(power, peak, mask), gain, mask.mask_id)
                if key in seen:
                    continue
                seen.add(key)
                south = min(p.lat_deg for p in self._points())
                if all(envelope(s.perigee_km, s.apogee_km, s.inclination_deg, mask, south) is None for s in mask_shapes):
                    not_visible.append({
                        "ntc_id": notice.ntc_id, "group_id": join["group_id"], "mask_id": mask.mask_id,
                        "reason": "orbit_cannot_reach_finland",
                        "orbit_shapes": [_shape_dict(s) for s in mask_shapes],
                        "finland_south_lat_deg": south,
                    })
                    continue
                if not _have_inputs(power, gain, mask_join, notice, low, high, mask, missing):
                    continue
                calculation = _ngso_calculation(
                    notice, mask_shapes, power, peak, gain, mask_join, assignment, mask, self._ngso_points(), south
                )
                calculation["calc_inputs"]["orbit_rows_incomplete"] = incomplete
                _add(calculation, power, screen, mask_join, notice, calculations, quality)

    def _gso_visible(self, lon: float, mask: LimitMask) -> bool:
        # Visibility depends on neither power nor the mask, so one answer per longitude will do.
        if lon not in self._visible:
            self._visible[lon] = evaluate_gso(lon, 0.0, 0.0, mask, self._points()).visible
        return self._visible[lon]

    def _points(self) -> tuple[GroundPoint, ...]:
        if self.points is None:
            self.points = finland_points()
        return self.points

    def _ngso_points(self) -> tuple[GroundPoint, ...]:
        return ngso_points(self._points())


@lru_cache(maxsize=None)
def ngso_points(points: tuple[GroundPoint, ...]) -> tuple[GroundPoint, ...]:
    """Whole-degree grid points, every 20th boundary vertex, and the four extremes.

    Coarser than the GSO grid because a flight tests each point many times. The envelope does not
    depend on it, and a sample is not a bound.
    """
    grid = [p for p in points if p.label == "grid" and p.lat_deg == int(p.lat_deg) and p.lon_deg == int(p.lon_deg)]
    boundary = [p for p in points if p.label == "boundary"][::20]
    extremes = [
        min(points, key=lambda p: p.lat_deg), max(points, key=lambda p: p.lat_deg),
        min(points, key=lambda p: p.lon_deg), max(points, key=lambda p: p.lon_deg),
    ]
    return tuple(sorted(set(grid + boundary + extremes), key=lambda p: (p.lat_deg, p.lon_deg)))


def _downlinks(notice: SpaceNotice) -> Iterable[dict[str, Any]]:
    return (
        a for a in notice.assignments
        if a.get("emi_rcp") == "E" and a.get("freq_min") is not None and a.get("freq_max") is not None
    )


def _note_excluded(quality: dict[tuple[Any, ...], dict[str, Any]], ntc_id: int, excluded: list[dict[str, Any]]) -> None:
    for row in excluded:
        quality.setdefault((ntc_id, row["group_id"], row["seq_no"], row["check"]), {"ntc_id": ntc_id, **row})


def _have_inputs(power, gain, join, notice, low, high, mask, missing) -> bool:
    if power is not None and gain is not None:
        return True
    missing.append({
        "group_id": join["group_id"],
        "beam_name": join["beam_name"],
        "ntc_id": notice.ntc_id,
        "mask_id": mask.mask_id,
        "missing": [name for name, value in (("pwr_ds_max", power), ("gain", gain)) if value is None],
        "reason": "power_or_gain_missing",
        "freq_min": low,
        "freq_max": high,
    })
    return False


def _add(calculation, power, screen, join, notice, calculations, quality) -> None:
    if calculation is None:
        return
    flags = _flags(power, screen, join, notice.ntc_id)
    if flags:
        calculation["data_quality_flags"] = flags
        for flag in flags:
            quality.setdefault((flag["ntc_id"], flag["group_id"], flag["seq_no"], flag["check"]), flag)
    calculations.append(calculation)


def pfd_summary(hits: Iterable[Hit]) -> dict[str, Any]:
    """Counts for reports: every record lands in exactly one result, reason or data-quality list."""
    results: Counter = Counter()
    bands: Counter = Counter()
    unevaluated: Counter = Counter()
    not_visible: Counter = Counter()
    data_quality: Counter = Counter()
    severity: Counter = Counter()
    masks = load_masks()
    for hit in hits:
        severity[hit.severity] += 1
        orbit, article = hit.rule_id.split("-")[1:]
        for calculation in hit.details["calculations"]:
            results[(orbit, article, calculation["result"])] += 1
            mask = masks.by_id(calculation["mask_id"])
            bands[(orbit, article, f"{mask.f_low_mhz:g}-{mask.f_high_mhz:g} MHz", calculation["result"])] += 1
        for row in hit.details["missing"]:
            unevaluated[(orbit, article, row["reason"], ",".join(row["missing"]))] += 1
        for row in hit.details["not_visible"]:
            not_visible[(orbit, article, row["reason"])] += 1
        for row in hit.details["data_quality"]:
            data_quality[(orbit, article, row["check"])] += 1
    return {
        "results": dict(sorted(results.items())),
        "bands": dict(sorted(bands.items())),
        "unevaluated": dict(sorted(unevaluated.items())),
        "not_visible": dict(sorted(not_visible.items())),
        "data_quality": dict(sorted(data_quality.items())),
        "severity": dict(sorted(severity.items())),
    }


def pfd_rule(settings: Settings) -> Rule:
    return PfdRule(settings)
