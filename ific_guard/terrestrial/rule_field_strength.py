"""P.1546 field strength for filtered land-mobile notices, against cited T/R 25-08 thresholds (ticket 10).

Deterministic: one notice's rows go in; a calculation, its parameters, assumptions and citations come
out. A notice that cannot be calculated becomes a finding of its own and never reads as clear. Whether
a value matters is for an expert: no result here says a regulation has been broken.
"""
from __future__ import annotations

import csv
import json
import logging
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import Settings
from ..db import session_scope
from ..engines.p1546 import IMPLEMENTATION, P1546RangeError, PathSegment, field_strength
from ..models import Artifact
from ..engines.pfd import MASK_DIR
from ..screening.rules import Hit
from ..screening.scoring import score_finding
from ..screening.space import MdbReader
from ..screening.stations import SIDE_RX, ProtectedBand, Station, load_stations
from ..sync.sources import TERRESTRIAL
from .adapter import (
    LAND_MOBILE_CLASSES,
    FilteredTerrestrialNotice,
    TerrestrialIssue,
    TerrestrialNotice,
    _distance_limit_mhz,
    _haversine_km,
    distance_to_finland,
    filter_near_finland,
    number,
)
from .diff import diff_issues

log = logging.getLogger(__name__)

RULE_ID = "terrestrial-p1546"
NOT_CALCULATED_RULE_ID = "terrestrial-not-calculated"

MASKS_PATH = MASK_DIR / "field_strength.json"
BOUNDARY_PATH = MASK_DIR / "finland_boundary.json"
LAND_PATH = MASK_DIR / "land_near_finland.json"

TIME_PERCENT = 10.0  # T/R 25-08 Annex 1, A1.2.3
RX_HEIGHT_M = 10.0  # T/R 25-08 Annex 1, A1.2.3
RX_CLUTTER_M = 10.0  # no effect for a rural receiver, whose reference is fixed at 10 m (P.1546 s.9)
ALTERNATIVE_TIME_PERCENT = 1.0  # T/R 25-08 Annex 1, footnote 2
ERP_FROM_EIRP_DB = -2.15  # gain of a half-wave dipole over an isotropic antenna
BOUNDARY_SPACING_KM = 2.0
WORST_POINT_SEARCH_KM = 150.0  # beyond the nearest boundary point; a design choice, stated in the output
PATH_SAMPLE_KM = 2.0
LAND_CELL_DEG = 0.02

# Engineering sanity checks, not regulatory values. Crossing one makes a data-quality item.
SANE_ERP_MAX_DBW = 60.0
SANE_HEIGHT_AGL_MAX_M = 500.0
SANE_EFFECTIVE_HEIGHT_ABS_MAX_M = 1500.0
SANE_IMPLIED_INPUT_MIN_DBW = -10.0  # 0.1 W into an antenna

STATION_LIST_MAX = 5

TR_DOC = "ECC Recommendation T/R 25-08 (edition 6 June 2025)"
TR_URL = "https://docdb.cept.org/download/4789"
TR_SCOPE_CITATION = {"doc": TR_DOC, "clause": "Introduction: scope 29.7-470 MHz", "page_or_url": f"p.2, {TR_URL}"}
TR_FOOTNOTE_CITATION = {"doc": TR_DOC, "clause": "Annex 1, A1.2.3, footnote 2", "page_or_url": f"p.6, {TR_URL}"}
TR_CONVERSION_CITATION = {"doc": TR_DOC, "clause": "Annex 1, A1.2.3, bandwidth conversion", "page_or_url": f"pp.6-7, {TR_URL}"}

COMMON_ASSUMPTIONS = (
    "Terrain is out of scope: no terrain profile, terrain clearance or tropospheric scatter. The direction of the error depends on the path.",
    "Land/sea segments come from Natural Earth 1:50m land, so they are approximate. Lakes count as land, including Ladoga, which biases paths over them low. Sea is treated as cold sea (ARCHITECTURE.md 4.6), which would bias a path low if warm-sea conditions apply.",
    "Notified e.r.p. is applied towards Finland from every notified transmit antenna. Antenna patterns (fxm_ant_diag) are not used, which biases directional stations high.",
    "Without an azimuth height table (fxm_ant_hgt), effective height is taken as the height above ground, because effective height cannot be derived from site altitude without terrain. This can bias either way. Where one antenna has a table, other antennas at the same height above ground use it, which is inferred from the data.",
    "Receiver: 10 m above ground, 50% of locations, rural surroundings. Time: 10%. Height, locations and time follow T/R 25-08 A1.2.3; rural is our choice. The transmitter is treated as open, with no clutter correction, which biases high where it stands in clutter.",
    "Evaluated at Natural Earth points on the Finnish boundary, including the coast. This is not the legal border, and T/R 25-08 does not define the evaluation point for a sea border.",
    "pwr_dbw with pwr_eiv E is read as e.r.p., and with I as e.i.r.p. This is inferred from the data, because the ITU ReadMe could not be read.",
    "Special-interest stations are matched on RX and TX bands, because Traficom said \"every TX is received in Finland\" (Discord, 2026-11-07). A field listed for a station with no separate RX match is at its site, using its antenna height, and is not the field at a receiver, which is elsewhere.",
)


@lru_cache(maxsize=1)
def _masks() -> dict:
    return json.loads(MASKS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _boundary_points() -> tuple[tuple[float, float], ...]:
    """(lat, lon) along the Finnish boundary rings, no further apart than BOUNDARY_SPACING_KM."""
    rings = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))["rings"]
    points = []
    for ring in rings:
        for (lon0, lat0), (lon1, lat1) in zip(ring, ring[1:] + ring[:1]):
            parts = max(1, math.ceil(_haversine_km(lat0, lon0, lat1, lon1) / BOUNDARY_SPACING_KM))
            points += [(lat0 + (lat1 - lat0) * i / parts, lon0 + (lon1 - lon0) * i / parts) for i in range(parts)]
    return tuple(points)


@lru_cache(maxsize=1)
def _land() -> tuple[dict, dict[int, list]]:
    """The box and the land edges by latitude band, so that a point test reads only nearby edges."""
    data = json.loads(LAND_PATH.read_text(encoding="utf-8"))
    bands: dict[int, list] = defaultdict(list)
    for ring in data["rings"]:
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
            if y0 != y1:
                for band in range(math.floor(min(y0, y1) * 4), math.floor(max(y0, y1) * 4) + 1):
                    bands[band].append((x0, y0, x1, y1))
    return data["box"], bands


class OutsideLandMask(ValueError):
    pass


@lru_cache(maxsize=200_000)
def _land_cell(lat_cell: int, lon_cell: int) -> bool:
    lat, lon = lat_cell * LAND_CELL_DEG, lon_cell * LAND_CELL_DEG
    box, bands = _land()
    if not (box["lat_min"] <= lat <= box["lat_max"] and box["lon_min"] <= lon <= box["lon_max"]):
        raise OutsideLandMask(f"({lat:.2f}, {lon:.2f}) is outside the land mask")
    inside = False
    for x0, y0, x1, y1 in bands.get(math.floor(lat * 4), ()):
        if (y0 > lat) != (y1 > lat) and lon < x0 + (lat - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def is_land(lat: float, lon: float) -> bool:
    return _land_cell(round(lat / LAND_CELL_DEG), round(lon / LAND_CELL_DEG))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    a1, a2, dlon = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(a2)
    x = math.cos(a1) * math.sin(a2) - math.sin(a1) * math.cos(a2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360


def _on_path(lat1: float, lon1: float, lat2: float, lon2: float, fraction: float) -> tuple[float, float]:
    """A point along the great circle, by interpolating unit vectors."""
    def vector(lat, lon):
        a, b = math.radians(lat), math.radians(lon)
        return math.cos(a) * math.cos(b), math.cos(a) * math.sin(b), math.sin(a)

    p, q = vector(lat1, lon1), vector(lat2, lon2)
    x, y, z = (p[i] + (q[i] - p[i]) * fraction for i in range(3))
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


def path_segments(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[PathSegment, ...]:
    """Land and cold-sea stretches from the transmitter outwards, sampled every PATH_SAMPLE_KM."""
    distance = _haversine_km(lat1, lon1, lat2, lon2)
    samples = max(1, math.ceil(distance / PATH_SAMPLE_KM))
    runs: list[list] = []
    for i in range(samples):
        land = is_land(*_on_path(lat1, lon1, lat2, lon2, (i + 0.5) / samples))
        if runs and runs[-1][0] == land:
            runs[-1][1] += 1
        else:
            runs.append([land, 1])
    return tuple(PathSegment(distance * count / samples, "land" if land else "cold_sea") for land, count in runs)


@dataclass(frozen=True)
class TxAntenna:
    ant_key: Any
    erp_dbw: float
    power_row: dict
    height_agl_m: float | None
    effective_by_azimuth: dict[float, float]
    effective_max_m: float | None
    site_altitude_m: float | None
    table_shared: bool = False

    @property
    def erp_kw(self) -> float:
        return 10 ** ((self.erp_dbw - 30) / 10)

    def effective_height(self, bearing: float) -> tuple[float, str]:
        if self.effective_by_azimuth:
            azimuth = min(self.effective_by_azimuth, key=lambda a: min(abs(a - bearing), 360 - abs(a - bearing)))
            shared = ", table of another antenna at the same height" if self.table_shared else ""
            return self.effective_by_azimuth[azimuth], f"fxm_ant_hgt, azimuth {azimuth:g} deg{shared}"
        if self.effective_max_m is not None:
            return self.effective_max_m, "fxm_ant.eff_hgtmax, no azimuth table"
        return self.height_agl_m, "fxm_ant.hgt_agl, no effective height notified"

    def sea_height(self) -> float:
        """An all-sea path needs height above the sea surface (P.1546 engine, tx_effective_height_m)."""
        return (self.height_agl_m or 0.0) + (self.site_altitude_m or 0.0)


class NotCalculable(Exception):
    def __init__(self, reason: str, explanation: str):
        super().__init__(explanation)
        self.reason, self.explanation = reason, explanation


def transmit_antennas(notice: TerrestrialNotice) -> list[TxAntenna]:
    """Antennas with usable inputs. Raises NotCalculable with the reason if there is none."""
    rows = [r for r in notice.raw["fxm_ant"] if r.get("tx_rx") == "TX"]
    if not rows:
        raise NotCalculable("no_transmit_antenna_row", "No transmit row in fxm_ant.")
    altitude = number(notice.raw["fxm_geo"].get("site_alt"))
    heights: dict[Any, dict[float, float]] = defaultdict(dict)
    for row in notice.raw["fxm_ant_hgt"]:
        azimuth, height = number(row.get("azm")), number(row.get("eff_hgt"))
        if azimuth is not None and height is not None:
            heights[row.get("ant_key")][azimuth % 360] = height
    usable, problems = [], Counter()
    for row in rows:
        power = number(row.get("pwr_dbw"))
        reference = row.get("pwr_eiv")
        agl, effective_max = number(row.get("hgt_agl")), number(row.get("eff_hgtmax"))
        by_azimuth = heights.get(row.get("ant_key"), {})
        shared = False
        if not by_azimuth and len(heights) == 1 and len({number(r.get("hgt_agl")) for r in rows}) == 1:
            # Notices give one table for several sectors at one height. Effective height belongs to the
            # site and direction, so sectors at the same height above ground use it too. Inferred from data.
            by_azimuth, shared = next(iter(heights.values())), True
        if power is None:
            problems["missing_power"] += 1
        elif reference not in ("E", "I"):
            problems["unknown_power_reference"] += 1
        elif agl is None and effective_max is None and not by_azimuth:
            problems["missing_antenna_height"] += 1
        else:
            erp = power + (ERP_FROM_EIRP_DB if reference == "I" else 0.0)
            usable.append(TxAntenna(row.get("ant_key"), erp, row, agl, by_azimuth, effective_max, altitude, shared))
    if not usable:
        reason = problems.most_common(1)[0][0]
        words = {
            "missing_power": "fxm_ant.pwr_dbw is empty.",
            "unknown_power_reference": "fxm_ant.pwr_eiv does not say e.r.p. or e.i.r.p.",
            "missing_antenna_height": "fxm_ant has no height above ground or effective height; none was assumed.",
        }
        raise NotCalculable(reason, words[reason])
    return usable


def _calculate(antenna: TxAntenna, frequency: float, segments: tuple[PathSegment, ...], bearing: float,
               time_percent: float = TIME_PERCENT, rx_height_m: float = RX_HEIGHT_M):
    effective, height_source = antenna.effective_height(bearing)
    if all(s.kind != "land" for s in segments):
        effective, height_source = antenna.sea_height(), "site altitude + height above ground, all-sea path"
    return field_strength(
        frequency_mhz=frequency,
        time_percent=time_percent,
        tx_effective_height_m=effective,
        rx_height_m=rx_height_m,
        rx_clutter_height_m=RX_CLUTTER_M,
        segments=segments,
        erp_kw=antenna.erp_kw,
        rx_area="rural",
        tx_height_agl_m=antenna.height_agl_m,
    ), height_source


def nearest_boundary_km(lat: float, lon: float) -> float:
    return min(_haversine_km(lat, lon, *point) for point in _boundary_points())


@dataclass(frozen=True)
class Worst:
    antenna: TxAntenna
    lat: float
    lon: float
    distance_km: float
    bearing_deg: float
    segments: tuple[PathSegment, ...]
    result: Any
    height_source: str
    points_evaluated: int
    points_rejected: int
    nearest_km: float


def worst_boundary_point(notice: TerrestrialNotice, antennas: list[TxAntenna]) -> Worst:
    lat, lon = notice.latitude_deg, notice.longitude_deg
    distances = [(_haversine_km(lat, lon, *p), p) for p in _boundary_points()]
    nearest = min(d for d, _ in distances)
    best, evaluated, rejected, last_error = None, 0, 0, None
    for distance, (point_lat, point_lon) in distances:
        if distance > nearest + WORST_POINT_SEARCH_KM:
            continue
        try:
            segments = path_segments(lat, lon, point_lat, point_lon)
        except OutsideLandMask as exc:
            raise NotCalculable("path_outside_land_mask", str(exc)) from exc
        bearing = _bearing_deg(lat, lon, point_lat, point_lon)
        for antenna in antennas:
            evaluated += 1
            try:
                result, source = _calculate(antenna, notice.frequency_mhz, segments, bearing)
            except P1546RangeError as exc:
                rejected, last_error = rejected + 1, str(exc)
                continue
            if best is None or result.field_strength_dbuv_m > best[0]:
                best = (result.field_strength_dbuv_m, antenna, point_lat, point_lon, distance, bearing, segments, result, source)
    if best is None:
        raise NotCalculable("outside_engine_range", f"P.1546 engine rejected every path: {last_error}")
    _, antenna, point_lat, point_lon, distance, bearing, segments, result, source = best
    return Worst(antenna, point_lat, point_lon, distance, bearing, segments, result, source, evaluated, rejected, nearest)


def _all_of(kind: str, worst: Worst, frequency: float, time_percent: float = TIME_PERCENT) -> float | None:
    try:
        return _calculate(worst.antenna, frequency, (PathSegment(worst.distance_km, kind),), worst.bearing_deg, time_percent)[0].field_strength_dbuv_m
    except P1546RangeError:
        return None


def annex1_mask(frequency_mhz: float) -> dict | None:
    """T/R 25-08 Annex 1 row for a frequency. Annex 3 and 4 need the Finnish system, which we do not have.

    At a shared band edge, the stricter value is used: the document does not say which applies.
    """
    rows = [m for m in _masks()["masks"] if m["annex"] == 1 and m["f_low_mhz"] <= frequency_mhz <= m["f_high_mhz"]]
    return min(rows, key=lambda m: m["breakpoints"][0][1]) if rows else None


def bandwidth_conversion_db(bandwidth_khz: float | None) -> float:
    """Annex 1 BC = 10 log10(channel spacing / 25 kHz), only above 25 kHz. Notices give bandwidth, not spacing."""
    return 10 * math.log10(bandwidth_khz / 25) if bandwidth_khz and bandwidth_khz > 25 else 0.0


@lru_cache(maxsize=4)
def _m4_rows(path: str) -> tuple[dict, ...]:
    with open(path, newline="", encoding="utf-8") as stream:
        return tuple(csv.DictReader(stream))


def m4_context(frequency_mhz: float | None, settings: Settings) -> dict | None:
    if frequency_mhz is None:
        return None
    for row in _m4_rows(str(settings.m4_filter_path)):
        if float(row["band_low_mhz"]) <= frequency_mhz < float(row["band_high_mhz"]):
            return {
                "band_mhz": [float(row["band_low_mhz"]), float(row["band_high_mhz"])],
                "primary_services": row["primary_services"],
                "finnish_terrestrial_use": row["finnish_terrestrial_use"],
                "terrestrial_primary": row["terrestrial_primary"] == "True",
                "m4_table_pages": row["m4_table_pages"],
            }
    return None


def _plain(value: Any) -> Any:
    """JSON-safe source rows, as Case.details is stored as JSON."""
    return json.loads(json.dumps(value, default=str, sort_keys=True))


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def data_quality_items(notice: TerrestrialNotice, antennas: list[TxAntenna]) -> list[str]:
    items = list(notice.quality_flags)
    geo = notice.raw["fxm_geo"]
    if geo.get("ctry") and geo["ctry"] != notice.adm:
        items.append(f"Site country {geo['ctry']} differs from the notifying administration {notice.adm}.")
    for antenna in antennas:
        row = antenna.power_row
        name = f"Antenna {antenna.ant_key}"
        if antenna.erp_dbw > SANE_ERP_MAX_DBW:
            items.append(f"{name}: e.r.p. {antenna.erp_dbw:g} dBW is implausibly high for land mobile.")
        if antenna.height_agl_m is not None and antenna.height_agl_m > SANE_HEIGHT_AGL_MAX_M:
            items.append(f"{name}: {antenna.height_agl_m:g} m above ground is implausibly high.")
        heights = [*antenna.effective_by_azimuth.values(), *([antenna.effective_max_m] if antenna.effective_max_m is not None else [])]
        if any(abs(h) > SANE_EFFECTIVE_HEIGHT_ABS_MAX_M for h in heights):
            items.append(f"{name}: an effective height beyond {SANE_EFFECTIVE_HEIGHT_ABS_MAX_M:g} m is implausible.")
        gain = number(row.get("gain_max"))
        if gain is not None:
            implied = antenna.erp_dbw - gain + 2.15
            if implied < SANE_IMPLIED_INPUT_MIN_DBW:
                items.append(
                    f"{name}: e.r.p. {antenna.erp_dbw:g} dBW with gain {gain:g} dBi implies {implied:.1f} dBW into the "
                    f"antenna. That is low for a base station on a {row.get('hgt_agl')} m mast, so the notified power "
                    "may be misread or mis-notified, and the calculated field strength may be too low."
                )
    return list(dict.fromkeys(items))


@dataclass
class PreviousIssue:
    """What the diff can use from the previous issue, and what happened when looking for it."""

    issue_no: int
    issue: TerrestrialIssue | None
    status: str  # available | no_artifact | file_missing
    sha256: str | None
    note: str

    def summary(self) -> dict:
        return {"issue_no": self.issue_no, "status": self.status, "sha256": self.sha256, "note": self.note}


def find_previous_issue(issue_no: int) -> PreviousIssue:
    """The latest stored revision of issue_no - 1.

    The diff classifies a notice from its own notified intent and target rows. The previous issue only
    corroborates, by confirming that a key or a modified target already existed. So screening carries on
    without it, says so on every finding, and does not report a row's absence as a withdrawal. A previous
    file that exists but cannot be read raises an error, which marks the screening failed.
    """
    previous_no = issue_no - 1
    with session_scope() as s:
        artifact = s.scalars(
            select(Artifact)
            .where(Artifact.domain == TERRESTRIAL, Artifact.issue_no == previous_no)
            .order_by(Artifact.id.desc())
        ).first()
        path, sha = (artifact.mdb_path, artifact.sha256) if artifact else (None, None)
    if path is None:
        note = (f"Issue {previous_no} is not stored. New, modified and withdrawn come from this issue's notified "
                "intent and target rows alone; they were not checked against the previous issue.")
        log.warning("terrestrial issue %s screened without issue %s", issue_no, previous_no)
        return PreviousIssue(previous_no, None, "no_artifact", None, note)
    if not Path(path).exists():
        note = (f"Issue {previous_no} is recorded, but its file is missing. New, modified and withdrawn come from "
                "this issue's notified intent and target rows alone.")
        log.warning("terrestrial issue %s: stored file of issue %s is missing", issue_no, previous_no)
        return PreviousIssue(previous_no, None, "file_missing", sha, note)
    previous = TerrestrialIssue(MdbReader(path), expected_issue=previous_no)
    note = f"Issue {previous_no} was read. Notified intent was cross-checked against its assignment keys."
    return PreviousIssue(previous_no, previous, "available", sha, note)


class FieldStrengthRule:
    """Calculate each filtered notice at the Finnish boundary, and compare with T/R 25-08 where it has a value."""

    id = RULE_ID
    emits = (RULE_ID, NOT_CALCULATED_RULE_ID)

    def __init__(self, settings: Settings, previous: PreviousIssue, sha256: str,
                 stations: list[Station] | None = None):
        self.settings = settings
        self.previous = previous
        self.sha256 = sha256
        self.stations = load_stations() if stations is None else stations
        self.funnel: dict[str, Any] = {}

    def run(self, issue: TerrestrialIssue) -> Iterable[Hit]:
        changes = self._changes(issue)
        neighbours = self.settings.neighbour_adms
        near = filter_near_finland(issue.notices, neighbour_adms=neighbours,
                                   distance_limits_km=self.settings.terrestrial_distance_limits_km)
        near_keys = {f.notice.terrakey for f in near}
        no_position = [n for n in issue.notices.values()
                       if n.adm in neighbours and n.latitude_deg is None and n.terrakey not in near_keys]
        summary = self._stage_counts(issue, near, no_position)

        hits: list[Hit] = []
        for notice in no_position:
            hits.append(self._not_calculated(
                issue, notice, None, changes[notice.terrakey], summary,
                NotCalculable("missing_coordinates", "Coordinates are missing or invalid, so the distance screen could not run."),
            ))
        for item in sorted(near, key=lambda f: int(f.notice.terrakey)):
            notice = item.notice
            try:
                hits.append(self._evaluate(issue, item, changes[notice.terrakey], summary))
            except NotCalculable as exc:
                hits.append(self._not_calculated(issue, notice, item, changes[notice.terrakey], summary, exc))
        self.funnel = self._funnel(issue, near, no_position, changes, hits, summary)
        return hits

    def _changes(self, issue: TerrestrialIssue) -> dict[str, dict]:
        available = self.previous.issue is not None
        reference = self.previous.issue or _EmptyIssue(self.previous.issue_no)
        return {
            c.terrakey: {
                "kind": c.kind,
                "target_ids": list(c.target_ids),
                "basis": c.basis,
                "previous_issue_no": self.previous.issue_no,
                "previous_issue_status": self.previous.status,
                "present_in_previous": c.present_in_previous if available else None,
                "target_in_previous": c.target_in_previous if available else None,
            }
            for c in diff_issues(reference, issue)
        }

    def _stage_counts(self, issue, near, no_position) -> dict[str, int]:
        notices = issue.notices
        in_adm = [n for n in notices.values() if n.adm in self.settings.neighbour_adms]
        published = {str(r["terrakey"]) for r in issue.publication_history}
        return {
            "published_in_issue": len(published),
            "land_mobile": len(notices),
            "neighbour_administrations": len(in_adm),
            "within_distance": len(near),
            "no_usable_position": len(no_position),
        }

    def _evaluate(self, issue, item: FilteredTerrestrialNotice, change: dict, stages: dict) -> Hit:
        notice = item.notice
        if change["kind"] == "withdrawn":
            raise NotCalculable("withdrawal", "The notice withdraws an assignment, so there is no new emission to calculate.")
        if notice.geometry_type != "POINT":
            raise NotCalculable("not_a_point_location", f"Geometry is {notice.geometry_type!r}; the calculation needs a transmitter point.")
        if notice.frequency_mhz is None:
            raise NotCalculable("missing_frequency", "freq_assgn is empty.")
        if not 30 <= notice.frequency_mhz <= 4000:
            raise NotCalculable("outside_engine_range", f"{notice.frequency_mhz:g} MHz is outside the P.1546 range of 30 to 4000 MHz.")
        antennas = transmit_antennas(notice)
        worst = worst_boundary_point(notice, antennas)
        frequency = notice.frequency_mhz
        value = worst.result.field_strength_dbuv_m

        mask = annex1_mask(frequency)
        conversion = bandwidth_conversion_db(notice.bandwidth_khz)
        limit = mask["breakpoints"][0][1] + conversion if mask else None
        margin = value - limit if limit is not None else None
        sensitivity = {
            "all_land_dbuv_m": _rounded(_all_of("land", worst, frequency)),
            "all_sea_dbuv_m": _rounded(_all_of("cold_sea", worst, frequency)),
            "time_1_percent_dbuv_m": _rounded(self._at_one_percent(worst, frequency)),
            "note": "At the worst point. All-land and all-sea bound the land/sea approximation at 10% time.",
        }
        crossed = self._crossed_by(value, limit, sensitivity)
        sensitivity["threshold_crossed_by"] = crossed

        if limit is None:
            assessment = "calculated_no_criterion"
        else:
            assessment = "above_indicative_threshold" if margin > 0 else "below_indicative_threshold"

        quality = data_quality_items(notice, antennas)
        m4 = m4_context(frequency, self.settings)
        stations = self._stations_affected(notice, worst.antenna)
        needs_info = self._needs_info(notice, mask)
        coordination = self._coordination(notice)

        details: dict[str, Any] = {
            "method": "p1546",
            "assessment": assessment,
            "calc_inputs": {
                **worst.result.calc_inputs,
                "implementation": worst.result.implementation,
                "h1_used_m": worst.result.h1_m,
                "effective_height_source": worst.height_source,
                "antenna": _plain({k: worst.antenna.power_row.get(k) for k in (
                    "ant_key", "pwr_dbw", "pwr_eiv", "pwr_xyz", "gain_max", "hgt_agl", "eff_hgtmax", "azm_max_e", "ant_dir")}),
                "erp_dbw": worst.antenna.erp_dbw,
                "site_altitude_m": worst.antenna.site_altitude_m,
                "search": {
                    "evaluation_points": "Natural Earth Finnish boundary, spaced at most 2 km, within 150 km of the nearest",
                    "calculations": worst.points_evaluated,
                    "rejected_by_engine": worst.points_rejected,
                    "nearest_boundary_km": round(worst.nearest_km, 3),
                },
            },
            "steps": {k: round(v, 4) for k, v in worst.result.steps.items()},
            "worst_point": {
                "lat": round(worst.lat, 5),
                "lon": round(worst.lon, 5),
                "distance_km": round(worst.distance_km, 3),
                "bearing_from_tx_deg": round(worst.bearing_deg, 1),
                "kind": "Finnish boundary, screening geometry",
            },
            "value_db": round(value, 2),
            "value_unit": "dB(uV/m)",
            "sensitivity": sensitivity,
            "assumptions": self._assumptions(worst, notice, mask),
            "framework": self._framework(notice, mask, m4),
            "criterion": self._criterion(notice, mask, conversion, limit),
            "needs_info": needs_info,
            "data_quality": quality,
            "change": change,
            "fxm_coord": coordination,
            "screen_distance": _screen_distance(item),
            "filter_counts": stages,
            "station_ids": sorted(s["station_id"] for s in stations),
            "stations_overlapping": stations[:STATION_LIST_MAX],
            "special_interest": bool(stations),
            "primary_allocation": bool(m4 and m4["terrestrial_primary"]),
            "passive_band": False,
            "citations": self._citations(notice, mask, m4),
            "source": self._source(issue, notice),
        }
        if limit is not None:
            details.update(limit_db=round(limit, 2), margin_db=round(margin, 2), mask_id=mask["mask_id"])

        ranking = score_finding(details)
        details["severity_score"] = ranking["score"]
        details["severity_reasons"] = ranking["reasons"]
        severity = self._severity(details)
        return Hit(RULE_ID, _family(notice, change), severity, self._title(notice, details, worst), notice.adm,
                   notice.raw["fxm_geo"].get("site_name"), [int(notice.terrakey)], details, None)

    def _at_one_percent(self, worst: Worst, frequency: float) -> float | None:
        try:
            return _calculate(worst.antenna, frequency, worst.segments, worst.bearing_deg, ALTERNATIVE_TIME_PERCENT)[0].field_strength_dbuv_m
        except P1546RangeError:
            return None

    @staticmethod
    def _crossed_by(value, limit, sensitivity) -> list[str]:
        """Which alternatives land on the other side of the threshold from the stated value."""
        if limit is None:
            return []
        names = {"all_land_dbuv_m": "all-land path", "all_sea_dbuv_m": "all-sea path", "time_1_percent_dbuv_m": "1% time"}
        return [name for key, name in names.items()
                if sensitivity[key] is not None and (sensitivity[key] > limit) != (value > limit)]

    def _assumptions(self, worst: Worst, notice, mask) -> list[str]:
        items = list(COMMON_ASSUMPTIONS)
        items += [a for a in [*worst.result.assumptions, *worst.result.warnings] if a not in items]
        items.append(self.previous.note)
        items.append("The distance screen uses an approximate polyline and an engineering limit. Neither is a regulatory value or a legal border.")
        if mask:
            items.append("The threshold is for co-channel use. No Finnish assignment list is available, so a co-channel Finnish system is assumed, not confirmed.")
            bandwidth = notice.bandwidth_khz
            if bandwidth is None:
                items.append("Bandwidth is missing, so no bandwidth conversion was applied, which gives the lower threshold.")
            elif bandwidth > 25:
                items.append("Notified bandwidth stands in for channel spacing in the Annex 1 conversion, with power assumed uniform across the channel.")
            else:
                items.append(f"Notified bandwidth of {bandwidth:g} kHz fits inside the 25 kHz reference bandwidth, so no conversion applies.")
        return items

    def _framework(self, notice, mask, m4) -> dict:
        terra = notice.raw["fxm_terra"]
        return {
            "itu": {
                "notified_provision": terra.get("prov"),
                "fragment": terra.get("fragment"),
                "note": "Verbatim from the notice, not interpreted. The repository's reference passages do not include RR Article 11 or the GE06 Agreement.",
            },
            "cept_ecc": {
                "applies": "T/R 25-08 Annex 1" if mask else None,
                "note": ("Indicative coordination threshold (recommends 2). Annexes 3 and 4 need the Finnish system type, and are not used."
                         if mask else "T/R 25-08 covers 29.7 to 470 MHz. No criterion for this frequency is in the repository; ECC decisions were not obtained."),
            },
            "national": ({**m4, "note": "From M4. No Finnish assignment list is available."} if m4 else
                         {"note": "No M4 row found for this frequency."}),
        }

    def _criterion(self, notice, mask, conversion, limit) -> dict:
        if mask is None:
            return {
                "status": "none_available",
                "statement": "No limit or trigger value in the repository applies, so the field strength is reported without a comparison.",
            }
        border = next((b for b in _masks()["borders"] if b["adm"] == notice.adm), None)
        return {
            "status": "indicative_threshold",
            "mask_id": mask["mask_id"],
            "threshold_dbuv_m_per_25khz": mask["breakpoints"][0][1],
            "bandwidth_conversion_db": round(conversion, 2),
            "limit_db": round(limit, 2),
            "evaluated_at": "border-line, 0 km",
            "time_percent": mask["time_percent"],
            "location_percent": mask["location_percent"],
            "rx_height_m": mask["rx_height_m"],
            "basis": mask["basis"],
            "border_applicability": border["notes"] if border else f"{notice.adm} has no entry in data/masks/field_strength.json.",
            "meaning": "Crossing it means coordination is indicated. It is not a limit on what may be notified.",
        }

    def _needs_info(self, notice, mask) -> list[str]:
        items = []
        if mask is None:
            items.append(f"Which criterion applies at {notice.frequency_mhz:g} MHz? None is in the repository, so no comparison was made.")
        else:
            items.append("Which time percentage does Traficom use, 10% or the 1% of footnote 2 for digital systems? (ticket 02)")
            items.append("Does a bilateral Finland agreement replace these values? None has been checked.")
            border = next((b for b in _masks()["borders"] if b["adm"] == notice.adm), None)
            if border and border.get("annex_5_country_type") is None:
                items.append(f"Do the T/R 25-08 values apply on the {border['country']} border? The document does not say.")
        return items

    @staticmethod
    def _coordination(notice) -> dict:
        rows = _plain([{k: r.get(k) for k in ("adm", "coord_prov", "coord_st", "decl_affct", "d_coord_st")} for r in notice.raw["fxm_coord"]])
        return {
            "fin_named": any(r["adm"] == "FIN" for r in rows),
            "fin_completed_declared_by": sorted({r["decl_affct"] for r in rows if r["adm"] == "FIN" and r["coord_st"] == "COORD COMPLETED" and r["decl_affct"]}),
            "rows": rows,
            "meaning": "Status text is verbatim; its meaning is inferred, as the ITU ReadMe could not be read. Check any stated completion against Traficom's own records.",
        }

    def _stations_affected(self, notice, antenna: TxAntenna) -> list[dict]:
        """Special-interest stations with an RX or TX band overlapping the notice, with the field at each site."""
        if notice.low_mhz is None or notice.high_mhz is None:
            return []
        found = []
        for station in self.stations:
            if station.lat_deg is None or station.lon_deg is None:
                continue
            bands = [b for b in station.protected_bands if b.overlaps(notice.low_mhz, notice.high_mhz)]
            if bands:
                found.append(self._field_at_station(notice, antenna, station, bands))
        return sorted(found, key=lambda s: (s["value_db"] is None, -(s["value_db"] or 0), s["station_id"]))

    def _field_at_station(self, notice, antenna, station: Station, bands: list[ProtectedBand]) -> dict:
        entry = {
            "station_id": station.key,
            "matched_bands": [{"side": b.side, "low_mhz": b.low_mhz, "high_mhz": b.high_mhz} for b in bands],
            "quality_flags": list(station.quality_flags),
            "data": "synthetic, from Traficom's special-interest list",
        }
        if not any(b.side == SIDE_RX for b in bands):
            entry["field_location"] = "station site, not the receiver: this band is received elsewhere"
        height = station.ant_height_agl_m
        distance = _haversine_km(notice.latitude_deg, notice.longitude_deg, station.lat_deg, station.lon_deg)
        entry.update(distance_km=round(distance, 2), value_db=None)
        try:
            if height is None:
                raise P1546RangeError("the station has no antenna height")
            segments = path_segments(notice.latitude_deg, notice.longitude_deg, station.lat_deg, station.lon_deg)
            bearing = _bearing_deg(notice.latitude_deg, notice.longitude_deg, station.lat_deg, station.lon_deg)
            result, _ = _calculate(antenna, notice.frequency_mhz, segments, bearing, rx_height_m=height)
            entry.update(value_db=round(result.field_strength_dbuv_m, 2), rx_height_m=height)
        except (P1546RangeError, OutsideLandMask) as exc:
            entry["not_calculated"] = str(exc)
        return entry

    @staticmethod
    def _severity(details: dict) -> str:
        assessment = details["assessment"]
        if assessment == "above_indicative_threshold":
            severity = "high" if details["severity_score"] >= 50 else "medium"
            declared = details["fxm_coord"]["fin_completed_declared_by"]
            if declared and severity == "high":
                severity = "medium"
                details["severity_reasons"] = [
                    *details["severity_reasons"],
                    f"medium, not high: fxm_coord says coordination with FIN is completed, declared by {', '.join(declared)}; verify",
                ]
        elif assessment == "calculated_no_criterion":
            severity = "medium"
        else:
            sensitive = details["sensitivity"]["threshold_crossed_by"]
            severity = "medium" if sensitive or details["fxm_coord"]["fin_named"] or details["special_interest"] else "low"
        if details["data_quality"] and severity == "high":
            severity = "medium"
            details["severity_reasons"] = [*details["severity_reasons"], "capped at medium: data-quality items"]
        return severity

    @staticmethod
    def _title(notice, details, worst) -> str:
        site = notice.raw["fxm_geo"].get("site_name") or notice.terrakey
        where = f"at {worst.distance_km:.0f} km on the Finnish boundary"
        head = f"{site} ({notice.adm}) {notice.frequency_mhz:g} MHz: {details['value_db']:.1f} dB(uV/m) {where}"
        assessment = details["assessment"]
        if assessment == "above_indicative_threshold":
            declared = details["fxm_coord"]["fin_completed_declared_by"]
            note = f"; {', '.join(declared)} declares FIN coordination completed, please verify" if declared else ""
            return f"Possible concern: {head}, {details['margin_db']:.1f} dB above the T/R 25-08 indicative threshold of {details['limit_db']:g}, under stated assumptions{note}"
        if assessment == "below_indicative_threshold":
            return f"{head}, {-details['margin_db']:.1f} dB below the T/R 25-08 indicative threshold of {details['limit_db']:g}, under stated assumptions"
        return f"Possible concern, needs expert criterion: {head}; no criterion in the repository, so no comparison"

    def _citations(self, notice, mask, m4) -> list[dict]:
        citations = [{"doc": IMPLEMENTATION["model"], "clause": "Annex 5, Annex 6", "page_or_url": f"DOI {IMPLEMENTATION['source_doi']}"}]
        if mask:
            citations += [mask["citation"], TR_FOOTNOTE_CITATION]
            if notice.bandwidth_khz and notice.bandwidth_khz > 25:
                citations.append(TR_CONVERSION_CITATION)
            border = next((b for b in _masks()["borders"] if b["adm"] == notice.adm), None)
            if border:
                citations.append(border["citation"])
        else:
            citations.append(TR_SCOPE_CITATION)
        if m4:
            citations.append({"doc": "M4", "clause": f"Frequency Allocation Table, {m4['band_mhz'][0]:g}-{m4['band_mhz'][1]:g} MHz",
                              "page_or_url": f"table pages {m4['m4_table_pages']}, https://opendata.traficom.fi/api/v13/Taajuusjakotaulukko"})
        return citations

    def _source(self, issue, notice) -> dict:
        raw = {k: v for k, v in notice.raw.items() if k != "fxm_ant_hgt"}
        return {
            "domain": TERRESTRIAL,
            "issue_no": issue.issue_no,
            "notice_ids": [notice.terrakey],
            "sha256": self.sha256,
            "rows": _plain(raw),
            "fxm_ant_hgt_rows": len(notice.raw["fxm_ant_hgt"]),
        }

    def _not_calculated(self, issue, notice, item, change, stages, problem: NotCalculable) -> Hit:
        withdrawal = problem.reason == "withdrawal"
        m4 = m4_context(notice.frequency_mhz, self.settings)
        mask = annex1_mask(notice.frequency_mhz) if notice.frequency_mhz else None
        details = {
            "method": "screening",
            "assessment": "not_calculated",
            "not_calculated_reason": problem.reason,
            "not_calculated_explanation": problem.explanation,
            "statement": "No field strength was calculated. This is not a clear result, so an expert should review it.",
            "assumptions": [self.previous.note, "No value was assumed in place of missing input."],
            "framework": self._framework(notice, mask, m4),
            "data_quality": list(notice.quality_flags),
            "change": change,
            "fxm_coord": self._coordination(notice),
            "screen_distance": _screen_distance(item) if item else None,
            "filter_counts": stages,
            "citations": [mask["citation"]] if mask else [TR_SCOPE_CITATION],
            "source": self._source(issue, notice),
            "severity_score": 0.0,
            "severity_reasons": ["not calculated: expert review needed"],
        }
        site = notice.raw["fxm_geo"].get("site_name") or notice.terrakey
        frequency = f"{notice.frequency_mhz:g} MHz" if notice.frequency_mhz else "unknown frequency"
        title = f"Not calculated, expert review needed: {site} ({notice.adm}) {notice.station_class} {frequency}: {problem.explanation}"
        return Hit(NOT_CALCULATED_RULE_ID, _family(notice, change), "low" if withdrawal else "medium", title, notice.adm,
                   notice.raw["fxm_geo"].get("site_name"), [int(notice.terrakey)], details, None)

    def _funnel(self, issue, near, no_position, changes, hits, stages) -> dict:
        assignments = issue.all_assignments
        published = {str(r["terrakey"]) for r in issue.publication_history}
        neighbours = list(self.settings.neighbour_adms)
        removed_class = Counter(assignments[k].get("stn_cls") for k in published if k not in issue.notices)
        non_land_neighbours = Counter(
            (assignments[k].get("adm"), assignments[k].get("stn_cls"))
            for k in published if k not in issue.notices and assignments[k].get("adm") in neighbours
        )
        by_adm = Counter(n.adm for n in issue.notices.values())
        near_keys = {f.notice.terrakey for f in near}
        dropped = [n for n in issue.notices.values()
                   if n.adm in neighbours and n.latitude_deg is not None and n.terrakey not in near_keys]
        limit = dict(self.settings.terrestrial_distance_limits_km)

        kinds_by_adm: dict[str, Counter] = defaultdict(Counter)
        for key, change in changes.items():
            kinds_by_adm[issue.notices[key].adm][change["kind"]] += 1

        adm_rows = {}
        for adm in neighbours:
            count = by_adm[adm]
            adm_rows[adm] = {
                "land_mobile_notices": count,
                "changes": dict(sorted(kinds_by_adm[adm].items())),
                "within_distance": sum(f.notice.adm == adm for f in near),
                "statement": (
                    "Notified no land-mobile station in this issue. Nothing was checked, so this is not a clean result."
                    if count == 0 else "Notices below were screened."
                ),
            }

        boundary_check = self._boundary_check(near, dropped)
        coord = self._fin_coordination(issue)
        reasons = Counter(h.details["not_calculated_reason"] for h in hits if h.rule_id == NOT_CALCULATED_RULE_ID)
        assessments = Counter(h.details["assessment"] for h in hits)
        severities = Counter(h.severity for h in hits)
        quality = sorted(str(h.notice_ids[0]) for h in hits if h.details["data_quality"])
        return {
            "issue_no": issue.issue_no,
            "previous_issue": self.previous.summary(),
            "stages": [
                {"stage": "published_in_issue", "kept": stages["published_in_issue"],
                 "why": "Distinct fxm_pub_hist keys for this circular, in fxm_terra. Broadcasting (fmtv_*) and other tables are out of scope and not read."},
                {"stage": "land_mobile", "kept": stages["land_mobile"], "removed": sum(removed_class.values()),
                 "removed_by_station_class": dict(sorted(removed_class.items(), key=lambda kv: str(kv[0]))),
                 "neighbour_notices_removed": {f"{a} {c}": n for (a, c), n in sorted(non_land_neighbours.items(), key=str)},
                 "neighbour_notices_removed_within_distance_limit": self._non_land_near(issue, published),
                 "why": f"Station classes {sorted(LAND_MOBILE_CLASSES)} only (ticket 08). Others are not land mobile, including FX and FL."},
                {"stage": "administration", "kept": stages["neighbour_administrations"],
                 "removed": stages["land_mobile"] - stages["neighbour_administrations"],
                 "removed_by_adm": {a: n for a, n in sorted(by_adm.items()) if a not in neighbours},
                 "why": f"Configured neighbours only: {neighbours}. This is a team decision. A station from another administration is not considered."},
                {"stage": "distance", "kept": stages["within_distance"], "removed": len(dropped),
                 "no_usable_position_flagged": stages["no_usable_position"],
                 "limit_km": limit,
                 "why": "Configured engineering limit, measured to an approximate polyline. Neither is a regulatory value or a legal border.",
                 **boundary_check},
                {"stage": "calculated", "kept": sum(h.rule_id == RULE_ID for h in hits),
                 "not_calculated": sum(reasons.values()), "not_calculated_by_reason": dict(sorted(reasons.items())),
                 "not_calculated_notices": sorted(str(h.notice_ids[0]) for h in hits if h.rule_id == NOT_CALCULATED_RULE_ID),
                 "why": "Each not-calculated notice is a finding for expert review. It is not a clear result."},
            ],
            "neighbours": adm_rows,
            "results": {
                "by_assessment": dict(sorted(assessments.items())),
                "by_severity": dict(sorted(severities.items())),
                "with_data_quality_items": quality,
            },
            "coordination_naming_finland": coord,
            "article11_reconciliation": issue.article11_reconciliation,
        }

    def _non_land_near(self, issue, published) -> int:
        """Not land mobile, but from a neighbour and inside the screen: the expert may want these."""
        geography = {r["geo_key"]: r for r in issue.reader.table("fxm_geo")}
        limits = self.settings.terrestrial_distance_limits_km
        count = 0
        for key in published - issue.notices.keys():
            row = issue.all_assignments[key]
            geo = geography.get(row.get("geo_key"), {})
            path = distance_to_finland(number(geo.get("lat_dec")), number(geo.get("long_dec")))
            if row.get("adm") in self.settings.neighbour_adms and path and path.distance_km <= _distance_limit_mhz(number(row.get("freq_assgn")), limits):
                count += 1
        return count

    def _boundary_check(self, near, dropped) -> dict:
        """Compare the polyline used by the screen with the Natural Earth boundary used for calculation."""
        limits = self.settings.terrestrial_distance_limits_km

        def beyond(notice) -> tuple[float, bool]:
            distance = nearest_boundary_km(notice.latitude_deg, notice.longitude_deg)
            return distance, distance > _distance_limit_mhz(notice.frequency_mhz, limits)

        passed = {f.notice.terrakey: beyond(f.notice) for f in near}
        removed = {n.terrakey: beyond(n) for n in dropped}
        return {
            "removed_nearest_km_natural_earth": round(min(d for d, _ in removed.values()), 1) if removed else None,
            "passed_furthest_km_natural_earth": round(max(d for d, _ in passed.values()), 1) if passed else None,
            "removed_but_within_limit_by_natural_earth": sorted(k for k, (_, out) in removed.items() if not out),
            "passed_but_beyond_limit_by_natural_earth": sorted(k for k, (_, out) in passed.items() if out),
        }

    @staticmethod
    def _fin_coordination(issue) -> dict:
        """fxm_coord rows naming Finland anywhere in this circular, so that the filter's misses can be seen."""
        published = {str(r["terrakey"]) for r in issue.publication_history}
        named = sorted({str(r["terrakey"]) for r in issue.reader.table("fxm_coord") if r.get("adm") == "FIN"} & published)
        return {
            "notices": named,
            "not_land_mobile": [k for k in named if k not in issue.notices],
            "meaning": "Inferred from the data, as the ITU ReadMe could not be read.",
        }


class _EmptyIssue:
    """Stands in for a missing previous issue, so the diff reads only the current notices."""

    def __init__(self, issue_no: int):
        self.issue_no = issue_no
        self.all_assignments: dict = {}


def _family(notice: TerrestrialNotice, change: dict) -> int:
    """A modification and the notice it modifies share a case, as in space."""
    modified = sorted(int(r["target"]) for r in notice.targets if r.get("action") == "MODIFY" and r.get("target") is not None)
    return modified[0] if change["kind"] == "modified" and modified else int(notice.terrakey)


def _screen_distance(item: FilteredTerrestrialNotice) -> dict:
    return {
        "polyline_km": round(item.distance_to_finland_km, 2),
        "limit_km": item.distance_limit_km,
        "precision": item.border_path.precision,
    }


def terrestrial_rules(settings: Settings, issue_no: int, sha256: str) -> list[FieldStrengthRule]:
    return [FieldStrengthRule(settings, find_previous_issue(issue_no), sha256)]
