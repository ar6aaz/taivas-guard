"""Non-GSO geometry for the pfd rule: an upper-bound envelope and a one-minute propagation.

Two different kinds of result, kept apart on purpose:

`envelope` needs only perigee, apogee and inclination. It holds for any node, phase, epoch and
argument of perigee, so nothing it leaves out can raise the margin within the notified altitudes.
It is a bound and may describe a state the satellite never occupies.

`propagate` flies one satellite for a day on its notified elements. It shows geometry the orbit
really produces, but samples one satellite from one assumed epoch, so it can miss the worst case.

For a notified orbit the true worst margin lies between the two. Both are spherical-Earth,
free-space calculations, using the constants in `engines.pfd`.
"""
import math
from dataclasses import dataclass, field
from functools import lru_cache

from .pfd import EARTH_RADIUS_KM, GroundPoint, LimitMask, spreading_loss_db

MU_KM3_S2 = 398600.4418
EARTH_ROTATION_DEG_PER_MIN = 360.98564736629 / 1440
ENVELOPE_STEP_DEG = 0.02  # includes every mask breakpoint; the steepest margin slope leaves under 0.02 dB between samples
PROPAGATION_MINUTES = 1440
PROPAGATION_STEP_MINUTES = 1


def reach_latitude_deg(inclination_deg: float) -> float:
    """The highest latitude directly under the orbit. A retrograde orbit reaches its supplement."""
    return min(inclination_deg, 180.0 - inclination_deg)


def min_central_angle_deg(inclination_deg: float, south_lat_deg: float) -> float:
    """The smallest Earth-central angle possible between the satellite and any point at or north of `south_lat_deg`."""
    return max(0.0, south_lat_deg - reach_latitude_deg(inclination_deg))


def slant_range_km(radius_km: float, angle_deg: float) -> float:
    """Distance from a point on the surface to a satellite at `radius_km`, seen at elevation `angle_deg`."""
    sin_a = math.sin(math.radians(angle_deg))
    cos_a = math.cos(math.radians(angle_deg))
    return math.sqrt(radius_km**2 - (EARTH_RADIUS_KM * cos_a) ** 2) - EARTH_RADIUS_KM * sin_a


def _min_radius_km(angle_deg: float, central_deg: float) -> float | None:
    """The lowest radius giving elevation `angle_deg` at central angle `central_deg`, or None if none does."""
    if central_deg == 0:
        return EARTH_RADIUS_KM  # directly overhead is possible, so no height is too low for any angle
    total = math.radians(angle_deg + central_deg)
    if total >= math.pi / 2:
        return None
    return EARTH_RADIUS_KM * math.cos(math.radians(angle_deg)) / math.cos(total)


@lru_cache(maxsize=None)
def _envelope_curve(perigee_km: float, apogee_km: float, central_deg: float) -> tuple[tuple[float, float, float], ...]:
    """(angle, altitude, spreading loss) at the closest reachable distance for each arrival angle.

    At a fixed angle, distance grows with altitude, so the worst state is the lowest altitude that
    can produce that angle: perigee, unless a low-inclination orbit must climb to see Finland at all.
    """
    perigee_radius = EARTH_RADIUS_KM + perigee_km
    apogee_radius = EARTH_RADIUS_KM + apogee_km
    curve = []
    for index in range(round(90 / ENVELOPE_STEP_DEG) + 1):
        angle = index * ENVELOPE_STEP_DEG
        needed = _min_radius_km(angle, central_deg)
        if needed is None or needed > apogee_radius:
            break  # the needed radius only grows with angle
        radius = max(perigee_radius, needed)
        curve.append((angle, radius - EARTH_RADIUS_KM, spreading_loss_db(slant_range_km(radius, angle))))
    return tuple(curve)


@dataclass(frozen=True)
class EnvelopeBest:
    """`relative_margin_db` is margin minus power density, reference bandwidth and gain, all in dB."""
    relative_margin_db: float
    angle_deg: float
    altitude_km: float
    distance_km: float
    limit_db: float
    central_angle_min_deg: float


_limit_tables: dict[str, tuple[float, ...]] = {}
_envelopes: dict[tuple[float, float, float, str, float], EnvelopeBest | None] = {}


def _limit_table(mask: LimitMask) -> tuple[float, ...]:
    if mask.mask_id not in _limit_tables:
        count = round(90 / ENVELOPE_STEP_DEG) + 1
        _limit_tables[mask.mask_id] = tuple(mask.limit_db(min(90.0, i * ENVELOPE_STEP_DEG)) for i in range(count))
    return _limit_tables[mask.mask_id]


def envelope(perigee_km: float, apogee_km: float, inclination_deg: float, mask: LimitMask, south_lat_deg: float) -> EnvelopeBest | None:
    """The largest possible margin for this orbit shape. None means Finland is always below its horizon."""
    key = (perigee_km, apogee_km, inclination_deg, mask.mask_id, south_lat_deg)
    if key not in _envelopes:
        _envelopes[key] = _envelope(perigee_km, apogee_km, inclination_deg, mask, south_lat_deg)
    return _envelopes[key]


def _envelope(perigee_km: float, apogee_km: float, inclination_deg: float, mask: LimitMask, south_lat_deg: float) -> EnvelopeBest | None:
    central = min_central_angle_deg(inclination_deg, south_lat_deg)
    curve = _envelope_curve(perigee_km, apogee_km if central > 0 else perigee_km, central)
    if not curve:
        return None
    limits = _limit_table(mask)
    best_margin, best = -math.inf, curve[0]
    for row, limit in zip(curve, limits):
        margin = -row[2] - limit
        if margin > best_margin:
            best_margin, best = margin, row
    angle, altitude, _ = best
    return EnvelopeBest(
        relative_margin_db=best_margin,
        angle_deg=angle,
        altitude_km=altitude,
        distance_km=slant_range_km(EARTH_RADIUS_KM + altitude, angle),
        limit_db=mask.limit_db(angle),
        central_angle_min_deg=central,
    )


@dataclass(frozen=True)
class Sample:
    angle_deg: float
    distance_km: float
    altitude_km: float
    lat_deg: float
    lon_deg: float
    minute: int


@dataclass(frozen=True)
class Propagation:
    """The closest sample in each 0.1 degree of arrival angle, so that masks can share one flight.

    Every mask limit rises or stays level with angle, and pfd rises as distance falls. Within a bin
    a sample's margin can therefore be beaten by under about 0.1 dB, and each one reported is a real
    position of the flight.
    """
    frontier: tuple[Sample, ...]
    minutes_with_finland_visible: int
    _best: dict[str, tuple[Sample, float, float] | None] = field(default_factory=dict, compare=False, repr=False)


@lru_cache(maxsize=None)
def propagate(
    perigee_km: float,
    apogee_km: float,
    inclination_deg: float,
    perigee_arg_deg: float,
    node_lon_deg: float,
    phase_deg: float,
    points: tuple[GroundPoint, ...],
) -> Propagation:
    """Two-body flight of one satellite. At minute 0 it is `phase_deg` past the ascending node."""
    perigee_radius = EARTH_RADIUS_KM + perigee_km
    apogee_radius = EARTH_RADIUS_KM + apogee_km
    a = (perigee_radius + apogee_radius) / 2
    e = (apogee_radius - perigee_radius) / (apogee_radius + perigee_radius)
    mean_motion = math.sqrt(MU_KM3_S2 / a**3) * 60  # radians per minute
    nu0 = math.radians(phase_deg - perigee_arg_deg)
    e0 = 2 * math.atan2(math.sqrt(1 - e) * math.sin(nu0 / 2), math.sqrt(1 + e) * math.cos(nu0 / 2))
    m0 = e0 - e * math.sin(e0)
    sin_i, cos_i = math.sin(math.radians(inclination_deg)), math.cos(math.radians(inclination_deg))
    omega = math.radians(perigee_arg_deg)

    unit = [
        (
            math.cos(math.radians(p.lat_deg)) * math.cos(math.radians(p.lon_deg)),
            math.cos(math.radians(p.lat_deg)) * math.sin(math.radians(p.lon_deg)),
            math.sin(math.radians(p.lat_deg)),
            p,
        )
        for p in points
    ]
    n = len(unit)
    centre = (sum(u[0] for u in unit) / n, sum(u[1] for u in unit) / n, sum(u[2] for u in unit) / n)
    norm = math.sqrt(sum(c * c for c in centre))
    cx, cy, cz = (c / norm for c in centre)
    cos_spread = min(cx * u[0] + cy * u[1] + cz * u[2] for u in unit)  # the region's angular radius
    sin_spread = math.sqrt(max(0.0, 1 - cos_spread**2))

    candidates: list[tuple[float, float, float, float, float, int]] = []
    visible_minutes = 0
    for minute in range(0, PROPAGATION_MINUTES, PROPAGATION_STEP_MINUTES):
        mean = m0 + mean_motion * minute
        ecc = mean
        if e > 0:
            for _ in range(30):  # Newton on Kepler's equation; converges well inside this for e < 0.9
                delta = (ecc - e * math.sin(ecc) - mean) / (1 - e * math.cos(ecc))
                ecc -= delta
                if abs(delta) < 1e-12:
                    break
            nu = 2 * math.atan2(math.sqrt(1 + e) * math.sin(ecc / 2), math.sqrt(1 - e) * math.cos(ecc / 2))
        else:
            nu = mean
        radius = a * (1 - e * math.cos(ecc))
        u = omega + nu
        theta = math.radians(node_lon_deg - EARTH_ROTATION_DEG_PER_MIN * minute)
        cos_t, sin_t, cos_u, sin_u = math.cos(theta), math.sin(theta), math.cos(u), math.sin(u)
        x = cos_t * cos_u - sin_t * sin_u * cos_i
        y = sin_t * cos_u + cos_t * sin_u * cos_i
        z = sin_u * sin_i
        cos_horizon = EARTH_RADIUS_KM / radius
        sin_horizon = math.sqrt(1 - cos_horizon**2)
        # Quick rejection against the whole region: the horizon plus the region's radius.
        if x * cx + y * cy + z * cz < cos_horizon * cos_spread - sin_horizon * sin_spread:
            continue
        any_visible = False
        for px, py, pz, point in unit:
            cos_psi = x * px + y * py + z * pz
            if cos_psi < cos_horizon:
                continue
            any_visible = True
            distance = math.sqrt(radius**2 + EARTH_RADIUS_KM**2 - 2 * radius * EARTH_RADIUS_KM * cos_psi)
            sin_angle = (radius * cos_psi - EARTH_RADIUS_KM) / distance
            candidates.append((sin_angle, distance, radius - EARTH_RADIUS_KM, point.lat_deg, point.lon_deg, minute))
        visible_minutes += any_visible

    closest: dict[int, tuple[float, float, float, float, float, int]] = {}
    for candidate in candidates:
        angle = math.degrees(math.asin(max(-1.0, min(1.0, candidate[0]))))
        slot = int(max(0.0, angle) * 10)
        if slot not in closest or candidate[1:] < closest[slot][1:]:
            closest[slot] = candidate
    frontier = tuple(
        Sample(max(0.0, math.degrees(math.asin(max(-1.0, min(1.0, row[0]))))), row[1], row[2], row[3], row[4], row[5])
        for _, row in sorted(closest.items())
    )
    return Propagation(frontier, visible_minutes)


def best_sample(propagation: Propagation, mask: LimitMask) -> tuple[Sample, float, float] | None:
    """The frontier sample with the largest margin, with its limit and its margin relative to power and gain."""
    if mask.mask_id not in propagation._best:
        best = None
        for sample in propagation.frontier:
            limit = mask.limit_db(sample.angle_deg)
            margin = -spreading_loss_db(sample.distance_km) - limit
            if best is None or margin > best[2]:
                best = (sample, limit, margin)
        propagation._best[mask.mask_id] = best
    return propagation._best[mask.mask_id]
