"""Power flux-density at the Earth's surface from a space station, against cited limits.

Pure and deterministic: takes plain numbers, reads no IFIC and no database. Free-space only, as
RR No. 21.16 defines its limits. The formulas are in ARCHITECTURE.md section 4.5; limits are data,
in data/masks/limit_masks.json, each with its citation.

A result is a screening calculation under the caller's assumptions. Using boresight gain towards
Finland over-estimates pfd wherever Finland is off-axis, so exceeding here is not proof of
non-compliance. Non-GSO orbits are ticket 07's: `pfd_db` and `LimitMask` work for them unchanged.
"""
import json
import math
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

EARTH_RADIUS_KM = 6378.0
GSO_RADIUS_KM = 42164.0
HIGHLY_INCLINED = {"inclination_deg_min": 35.0, "inclination_deg_max": 145.0, "apogee_km_gt": 18000.0}  # RR 21.16.17
# The Compose volume covers /app/data, so the image keeps masks elsewhere and sets IFIC_MASK_DIR.
MASK_DIR = Path(os.environ.get("IFIC_MASK_DIR") or Path(__file__).resolve().parents[2] / "data" / "masks")


@dataclass(frozen=True)
class Geometry:
    distance_km: float
    angle_deg: float  # angle of arrival above the horizontal plane


@dataclass(frozen=True)
class GroundPoint:
    lat_deg: float
    lon_deg: float
    label: str = ""


def gso_geometry(lat_deg: float, lon_deg: float, gso_lon_deg: float) -> Geometry | None:
    """Slant range and angle of arrival on a spherical Earth. None when below the horizon."""
    cos_psi = math.cos(math.radians(lat_deg)) * math.cos(math.radians(gso_lon_deg - lon_deg))
    if cos_psi < EARTH_RADIUS_KM / GSO_RADIUS_KM:
        return None
    distance_km = math.sqrt(EARTH_RADIUS_KM**2 + GSO_RADIUS_KM**2 - 2 * EARTH_RADIUS_KM * GSO_RADIUS_KM * cos_psi)
    sin_psi = math.sqrt(max(0.0, 1 - cos_psi**2))
    return Geometry(distance_km, math.degrees(math.atan2(cos_psi - EARTH_RADIUS_KM / GSO_RADIUS_KM, sin_psi)))


def spreading_loss_db(distance_km: float) -> float:
    if distance_km <= 0:
        raise ValueError("distance_km must be positive")
    return 10 * math.log10(4 * math.pi * (distance_km * 1000) ** 2)


def pfd_db(
    p_density_dbw_hz: float, ref_bw_hz: float, gain_dbi: float, distance_km: float, peak_power_dbw: float | None = None
) -> float:
    """pfd in dB(W/m2) in the reference bandwidth.

    Density times bandwidth assumes the emission fills the reference bandwidth. A narrower one can't
    put more than its total power there, so `peak_power_dbw`, when known, caps the result.
    """
    if ref_bw_hz <= 0:
        raise ValueError("ref_bw_hz must be positive")
    power_dbw = p_density_dbw_hz + 10 * math.log10(ref_bw_hz)
    if peak_power_dbw is not None:
        power_dbw = min(power_dbw, peak_power_dbw)
    return power_dbw + gain_dbi - spreading_loss_db(distance_km)


def x_db(n_satellites: int, n_visible: int | None = None) -> float:
    """Function X in RR No. 21.16.6, for the 17.7-19.3 GHz non-GSO row."""
    n = n_satellites
    if n <= 0:
        raise ValueError("n_satellites must be positive")
    if n <= 50:
        return 0.0
    if n <= 288:
        return (5 / 119) * (n - 50)
    if n <= 999:
        return (1 / 69) * (n + 402)
    if n_visible is None or n_visible <= 0:
        raise ValueError("Above 999 satellites, RR No. 21.16.6 needs the number of visible satellites, Nv")
    return max(20.3, 10 * math.log10(n_visible)) if n <= 6000 else 10 * math.log10(n_visible) + 1


def y_db(max_co_frequency_per_hemisphere: int) -> float:
    """Function Y in RR No. 21.16.15, for the 3.4-4.2 GHz non-GSO row. Takes max(NN, NS)."""
    n = max_co_frequency_per_hemisphere
    if n <= 0:
        raise ValueError("max_co_frequency_per_hemisphere must be positive")
    return 0.0 if n <= 2 else 5 * math.log10(n)


def _interpolate(points: list[list[float]], x: float) -> float:
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    raise ValueError(f"{x} is beyond the last breakpoint")


@dataclass(frozen=True)
class LimitMask:
    mask_id: str
    kind: str  # pfd_art21 is a compliance limit; pfd_app5 is a coordination trigger
    f_low_mhz: float
    f_high_mhz: float
    services: list[str]
    orbit_type: str | None  # None means any
    ref_bw_hz: float
    breakpoints: list[list[float]]
    unit: str
    citation: dict[str, Any]
    applies_when: dict[str, Any] | None = None
    parameter: dict[str, Any] | None = None  # the mask's X or Y
    note: str | None = None

    def limit_db(self, angle_deg: float, parameter_db: float | None = None) -> float:
        if not 0 <= angle_deg <= 90:
            raise ValueError(f"angle_deg must be 0-90, got {angle_deg}")
        limit = _interpolate(self.breakpoints, angle_deg)
        if self.parameter is None:
            return limit
        if parameter_db is None:
            raise ValueError(f"Mask {self.mask_id} needs {self.parameter['name']}, from its citation's footnote")
        return limit - parameter_db * _interpolate(self.parameter["weights"], angle_deg)

    def overlaps(self, f_low_mhz: float, f_high_mhz: float) -> bool:
        return f_low_mhz < self.f_high_mhz and f_high_mhz > self.f_low_mhz


@dataclass(frozen=True)
class MaskSet:
    masks: tuple[LimitMask, ...]
    no_limit_for_finland: tuple[dict[str, Any], ...]
    not_covered: tuple[dict[str, Any], ...]

    def by_id(self, mask_id: str) -> LimitMask:
        return next(m for m in self.masks if m.mask_id == mask_id)

    def select(
        self,
        f_low_mhz: float,
        f_high_mhz: float,
        kind: str,
        orbit_type: str,
        inclination_deg: float | None = None,
        apogee_km: float | None = None,
    ) -> list[LimitMask]:
        """Every loaded mask that could apply. An empty list isn't a pass: check `gaps` as well.

        Non-GSO rows split on orbit shape (RR 21.16.17 and 21.16.18). Without the orbit, both stay
        in, so the caller can take the worst and say so.
        """
        def fits(m: LimitMask) -> bool:
            if m.kind != kind or not m.overlaps(f_low_mhz, f_high_mhz) or m.orbit_type not in (None, orbit_type):
                return False
            when = m.applies_when or {}
            heo = _highly_inclined(inclination_deg, apogee_km)
            if heo is None:
                return True
            return not (when.get("not_highly_inclined") and heo) and not ("apogee_km_gt" in when and not heo)

        return [m for m in self.masks if fits(m)]

    def gaps(self, f_low_mhz: float, f_high_mhz: float) -> list[dict[str, Any]]:
        """Why a band may have no mask here: no limit applies to Finland, or it isn't transcribed yet."""
        rows = [{**g, "status": "no_limit_for_finland"} for g in self.no_limit_for_finland]
        rows += [{**g, "status": "not_covered"} for g in self.not_covered]
        return [g for g in rows if f_low_mhz < g["f_high_mhz"] and f_high_mhz > g["f_low_mhz"]]


def _highly_inclined(inclination_deg: float | None, apogee_km: float | None) -> bool | None:
    if inclination_deg is None or apogee_km is None:
        return None
    return (
        HIGHLY_INCLINED["inclination_deg_min"] <= inclination_deg <= HIGHLY_INCLINED["inclination_deg_max"]
        and apogee_km > HIGHLY_INCLINED["apogee_km_gt"]
    )


@lru_cache
def load_masks(path: Path = MASK_DIR / "limit_masks.json") -> MaskSet:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    return MaskSet(tuple(LimitMask(**m) for m in raw["masks"]), tuple(raw["no_limit_for_finland"]), tuple(raw["not_covered"]))


def _inside(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        if (y0 > lat) != (y1 > lat) and lon < x0 + (lat - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


@lru_cache
def finland_points(step_deg: float = 0.5, path: Path = MASK_DIR / "finland_boundary.json") -> tuple[GroundPoint, ...]:
    """A grid over Finland plus every boundary vertex.

    The boundary matters: the lowest angle of arrival, and so often the strictest limit, falls at
    the edge of the country, which a grid alone would miss by up to half a step.
    """
    with path.open(encoding="utf-8") as f:
        rings = json.load(f)["rings"]
    vertices = [c for ring in rings for c in ring]
    lons, lats = [c[0] for c in vertices], [c[1] for c in vertices]
    points = {(round(lat, 5), round(lon, 5)): "boundary" for lon, lat in vertices}
    lat = math.floor(min(lats) / step_deg) * step_deg
    while lat <= max(lats):
        lon = math.floor(min(lons) / step_deg) * step_deg
        while lon <= max(lons):
            if any(_inside(lon, lat, ring) for ring in rings):
                points.setdefault((round(lat, 5), round(lon, 5)), "grid")
            lon += step_deg
        lat += step_deg
    return tuple(GroundPoint(la, lo, label) for (la, lo), label in sorted(points.items()))


@dataclass(frozen=True)
class WorstPoint:
    lat_deg: float
    lon_deg: float
    label: str
    distance_km: float
    angle_deg: float
    value_db: float
    limit_db: float
    margin_db: float  # positive means above the limit


@dataclass(frozen=True)
class PfdResult:
    visible: bool  # False means below the horizon from every point; there is no number to report
    mask_id: str
    mask_kind: str
    citation: dict[str, Any]
    unit: str
    ref_bw_hz: float
    points_evaluated: int
    points_visible: int
    worst: WorstPoint | None
    calc_inputs: dict[str, Any]
    assumptions: tuple[str, ...]

    @property
    def above_limit(self) -> bool:
        return self.worst is not None and self.worst.margin_db > 0

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "above_limit": self.above_limit}


ASSUMPTIONS = (
    "Free-space propagation (RR No. 21.16).",
    "Angle of arrival is geometric elevation on a spherical Earth, with no refraction.",
    "The given gain applies towards every point. With boresight gain this over-estimates pfd off-axis.",
)


def evaluate_gso(
    gso_lon_deg: float,
    p_density_dbw_hz: float,
    gain_dbi: float,
    mask: LimitMask,
    points: Iterable[GroundPoint] | None = None,
    peak_power_dbw: float | None = None,
    parameter_db: float | None = None,
) -> PfdResult:
    """Evaluates one emission against one mask at each point, and reports the largest margin."""
    # A station's location is often the case that matters, so callers add stations with finland_points().
    candidates = tuple(points) if points is not None else finland_points()
    worst: WorstPoint | None = None
    visible = 0
    for point in candidates:
        geometry = gso_geometry(point.lat_deg, point.lon_deg, gso_lon_deg)
        if geometry is None:
            continue
        visible += 1
        value = pfd_db(p_density_dbw_hz, mask.ref_bw_hz, gain_dbi, geometry.distance_km, peak_power_dbw)
        limit = mask.limit_db(geometry.angle_deg, parameter_db)
        if worst is None or value - limit > worst.margin_db:
            worst = WorstPoint(
                point.lat_deg, point.lon_deg, point.label, geometry.distance_km, geometry.angle_deg, value, limit, value - limit
            )
    return PfdResult(
        visible=worst is not None,
        mask_id=mask.mask_id,
        mask_kind=mask.kind,
        citation=mask.citation,
        unit=mask.unit,
        ref_bw_hz=mask.ref_bw_hz,
        points_evaluated=len(candidates),
        points_visible=visible,
        worst=worst,
        calc_inputs={
            "gso_lon_deg": gso_lon_deg,
            "p_density_dbw_hz": p_density_dbw_hz,
            "gain_dbi": gain_dbi,
            "peak_power_dbw": peak_power_dbw,
            "parameter_db": parameter_db,
            "earth_radius_km": EARTH_RADIUS_KM,
            "gso_radius_km": GSO_RADIUS_KM,
        },
        assumptions=ASSUMPTIONS,
    )
