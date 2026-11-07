"""Read current-circular land-mobile notices, retaining source rows and units."""
from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property
import math
from typing import Any, Mapping

from ..screening.space import TableReader

LAND_MOBILE_CLASSES = {"FB": "Base station", "ML": "Land mobile station"}
FINNISH_BORDER_POINTS = (
    (60.0, 27.8), (61.0, 28.9), (62.0, 30.9), (63.5, 31.5),
    (65.0, 29.8), (67.0, 29.5), (68.8, 28.5), (69.8, 29.0),
    (70.05, 27.5), (69.0, 20.5), (66.0, 23.7), (60.0, 20.0),
    (59.9, 23.0), (59.9, 25.5),
)


def number(value: Any) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _pub_key(row: dict) -> tuple:
    return tuple(row.get(k) for k in ("pub_no", "pub_prefix", "addendum", "corrigendum"))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = lat2 - lat1, math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _nearest_border_point(lat: float, lon: float) -> tuple[float, float, float]:
    """Return the nearest point on the screening polyline and its distance.

    The polyline is deliberately an approximate geography for pre-screening.
    Ticket 09 can replace it with a coastline/border path before P.1546.
    """
    best = (math.inf, None, None)
    cos_lat = max(math.cos(math.radians(lat)), 0.1)
    for (lat1, lon1), (lat2, lon2) in zip(FINNISH_BORDER_POINTS, FINNISH_BORDER_POINTS[1:]):
        x1, y1 = lon1 * cos_lat, lat1
        x2, y2 = lon2 * cos_lat, lat2
        dx, dy = x2 - x1, y2 - y1
        fraction = 0.0 if dx * dx + dy * dy == 0 else ((lon * cos_lat - x1) * dx + (lat - y1) * dy) / (dx * dx + dy * dy)
        fraction = min(1.0, max(0.0, fraction))
        candidate = (lat1 + fraction * (lat2 - lat1), lon1 + fraction * (lon2 - lon1))
        distance = _haversine_km(lat, lon, *candidate)
        if distance < best[0]:
            best = (distance, *candidate)
    distance, border_lat, border_lon = best
    return float(distance), float(border_lat), float(border_lon)


@dataclass(frozen=True)
class BorderPath:
    """Minimal path handoff for P.1546; coastline segmentation is engine-owned."""

    distance_km: float
    border_latitude_deg: float
    border_longitude_deg: float
    land_sea_segments: tuple[tuple[float, str], ...] | None = None
    precision: str = "approximate screening polyline; not a legal border or coastline"
    note: str = "Land/sea segmentation requires the P.1546 coastline dataset."


@dataclass(frozen=True)
class FilteredTerrestrialNotice:
    notice: "TerrestrialNotice"
    distance_to_finland_km: float
    distance_limit_km: float
    border_path: BorderPath


@dataclass
class TerrestrialNotice:
    terrakey: str
    adm: str
    station_class: str
    frequency_mhz: float | None
    low_mhz: float | None
    high_mhz: float | None
    bandwidth_khz: float | None
    latitude_deg: float | None
    longitude_deg: float | None
    geometry_type: str | None
    radius_km: float | None
    intent: str | None
    targets: list[dict]
    nature_services: list[str]
    raw: dict[str, Any]
    quality_flags: list[str]


class TerrestrialIssue:
    """A circular is an update, not a complete inventory of active assignments.

    Membership comes from pub + fxm_pub_hist, including special sections whose
    publication number differs from the circular number. Older recorded target
    assignments in the same MDB are retained in all_assignments but not emitted.
    """

    def __init__(self, reader: TableReader, expected_issue: int | None = None):
        self.reader = reader
        current = reader.table("pub_current_circ")
        if len(current) != 1 or current[0].get("circ_no") is None:
            raise ValueError("Terrestrial database must identify exactly one current circular.")
        self.issue_no = int(current[0]["circ_no"])
        if expected_issue is not None and self.issue_no != expected_issue:
            raise ValueError(f"Expected terrestrial issue {expected_issue}, database contains {self.issue_no}.")
        self.published_on = str(current[0].get("d_pub") or "")[:10] or None
        refs = {r["stn_cls"]: r["description"] for r in reader.table("ref_stn_cls")}
        for code, label in LAND_MOBILE_CLASSES.items():
            if refs.get(code, "").strip().lower() != label.lower():
                raise ValueError(f"Unsupported terrestrial station-class definition: {code}: {refs.get(code)!r}")
        self.nature_service_descriptions = {r["nat_srv"]: r["description"] for r in reader.table("ref_nat_srv")}
        publications = [r for r in reader.table("pub") if r.get("circ_no") == self.issue_no]
        if not publications:
            raise ValueError(f"Missing publication index for terrestrial circular {self.issue_no}.")
        keys = {_pub_key(r) for r in publications}
        self.current_publications = publications
        self.publication_history = [r for r in reader.table("fxm_pub_hist") if _pub_key(r) in keys]
        self.published_totals = [r for r in reader.table("pub_totals") if _pub_key(r) in keys]

    @cached_property
    def all_assignments(self) -> dict[str, dict]:
        result = {}
        for row in self.reader.table("fxm_terra"):
            key = str(row["terrakey"])
            if key in result:
                raise ValueError(f"Duplicate terrestrial terrakey: {key}")
            result[key] = row
        return result

    @cached_property
    def notices(self) -> dict[str, TerrestrialNotice]:
        histories = defaultdict(list)
        for row in self.publication_history:
            histories[str(row["terrakey"])].append(row)
        missing = histories.keys() - self.all_assignments.keys()
        if missing:
            raise ValueError(f"Publication history references missing assignments: {sorted(missing)[:5]}")
        geography = {r["geo_key"]: r for r in self.reader.table("fxm_geo")}
        attached = {}
        for table in ("fxm_ant", "fxm_ant_hgt", "fxm_nat_serv", "fxm_target", "fxm_coord"):
            grouped = defaultdict(list)
            for row in self.reader.table(table):
                grouped[str(row["terrakey"])].append(row)
            attached[table] = grouped
        result = {}
        for key in sorted(histories):
            row = self.all_assignments[key]
            if row.get("stn_cls") not in LAND_MOBILE_CLASSES:
                continue
            geo = geography.get(row.get("geo_key"), {})
            lat, lon = number(geo.get("lat_dec")), number(geo.get("long_dec"))
            flags = []
            if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                lat = lon = None
                flags.append("Missing or invalid coordinates; retain for review.")
            low, high, center = (number(row.get(k)) for k in ("freq_min", "freq_max", "freq_assgn"))
            bandwidth = number(row.get("bdwdth"))
            if low is None or high is None or low <= 0 or high < low:
                low = high = None
                if center is not None and center > 0 and bandwidth is not None and bandwidth > 0:
                    low, high = center - bandwidth / 2000, center + bandwidth / 2000
                    flags.append("Frequency interval derived from assigned MHz and bandwidth kHz.")
                else:
                    flags.append("Missing or invalid frequency interval; retain for review.")
            nature = [r["nat_srv"] for r in attached["fxm_nat_serv"][key]]
            if any(n not in self.nature_service_descriptions for n in nature):
                flags.append("Unrecognized nature-of-service code.")
            raw = {"fxm_terra": row, "fxm_geo": geo, "fxm_pub_hist": histories[key],
                   **{table: grouped[key] for table, grouped in attached.items()}}
            result[key] = TerrestrialNotice(
                key, row.get("adm") or "", row["stn_cls"], center, low, high, bandwidth,
                lat, lon, geo.get("geo_type"), number(geo.get("radius")), row.get("intent"),
                attached["fxm_target"][key], nature, raw, flags,
            )
        return result

    @property
    def article11_totals(self) -> dict[str, int]:
        """Published Article 11 totals, grouped by the circular's NTFD parts."""
        return {
            str(row["pub_part"]): int(row["no_publish"])
            for row in self.published_totals
            if row.get("pub_prefix") == "NTFD_RR" and row.get("pub_part") is not None
        }

    @property
    def published_assignment_count(self) -> int:
        return sum(self.article11_totals.values())

    @property
    def article11_reconciliation(self) -> dict[str, int]:
        """Return explicit counts; Article 11 includes more than this adapter's rows."""
        assignments = self.all_assignments.values()
        notice_rows = sum(str(row.get("is_notice", "")).upper() == "TRUE" for row in assignments)
        return {
            "article11_total": self.published_assignment_count,
            "fxm_terra_rows": len(self.all_assignments),
            "fxm_notice_rows": notice_rows,
            "fxm_recorded_rows": len(self.all_assignments) - notice_rows,
            "publication_history_rows": len(self.publication_history),
            "unreconciled_article11_minus_notice_rows": self.published_assignment_count - notice_rows,
        }


def distance_to_finland(lat: float | None, lon: float | None) -> BorderPath | None:
    if lat is None or lon is None:
        return None
    distance, border_lat, border_lon = _nearest_border_point(lat, lon)
    return BorderPath(distance, border_lat, border_lon)


def _distance_limit_mhz(frequency_mhz: float | None, limits: Mapping[str, float]) -> float:
    default = limits.get("default")
    matches = []
    if frequency_mhz is not None:
        for key, limit in limits.items():
            if key == "default":
                continue
            try:
                low, high = (float(part) for part in key.split("-", 1))
            except (TypeError, ValueError):
                raise ValueError(f"Invalid terrestrial distance band {key!r}; use 'low-high' in MHz.") from None
            if low <= frequency_mhz <= high:
                matches.append((high - low, float(limit)))
    if matches:
        return min(matches)[1]
    if default is None:
        raise ValueError("Terrestrial distance limits need a matching band or a 'default' value.")
    return float(default)


def filter_near_finland(
    notices: Mapping[str, TerrestrialNotice],
    *,
    neighbour_adms: list[str] | tuple[str, ...] | None = None,
    distance_limits_km: Mapping[str, float] | None = None,
) -> list[FilteredTerrestrialNotice]:
    """Keep configured neighbouring administrations within the configured screen distance."""
    if neighbour_adms is None:
        from ..config import get_settings
        settings = get_settings()
        neighbour_adms = settings.neighbour_adms
        distance_limits_km = settings.terrestrial_distance_limits_km if distance_limits_km is None else distance_limits_km
    limits = distance_limits_km or {"default": 300.0}
    filtered = []
    for notice in notices.values():
        if notice.adm not in neighbour_adms:
            continue
        path = distance_to_finland(notice.latitude_deg, notice.longitude_deg)
        if path is None:
            continue
        limit = _distance_limit_mhz(notice.frequency_mhz, limits)
        if path.distance_km <= limit:
            filtered.append(FilteredTerrestrialNotice(notice, path.distance_km, limit, path))
    return filtered
