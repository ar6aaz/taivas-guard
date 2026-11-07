"""Field strength by Recommendation ITU-R P.1546-6 (08/2019). Pure: plain numbers in.

Call :func:`field_strength`. It reads no IFIC data, database or terrain, and the
same inputs always give the same result. Values outside the Recommendation's
valid range raise :class:`P1546RangeError` rather than returning a number.

The result is for 50% of locations, the only value T/R 25-08 asks for. Terrain
inputs (``pathinfo = 1``) and Annex 7 (other climates) are not offered.

Provenance
    Python port of the OFCOM reference implementation ``eeveetza/p1546`` v6.2,
    commit 0e42639, DOI 10.5281/zenodo.6206015, by Ivica Stevanovic (OFCOM,
    Switzerland). ``p1546_NOTICE.txt`` holds the upstream licence and the record
    of changes. It runs without MATLAB or Octave, and is checked against the
    upstream validation logs in ``tests/test_p1546.py``.

Deliberate differences from the upstream code, each following the Recommendation
    1. Annex 5 s.4.2, E': upstream divides log10(h1/10) by the natural log(2).
       Both logs here have the same base.
    2. Annex 5 s.8: on a mixed path with h1 < 3 m, Esea uses 3 m. Upstream uses h1.
    3. All-sea path with 1 <= h1 < 3 m: computed at h1 with a warning (s.3.3 calls
       this unreliable). Upstream silently raises h1 to 3 m.
    4. Upstream clamps h1 > 3000 m and substitutes heff for a missing ha. These
       raise an error here, as do all out-of-range inputs.
"""

from __future__ import annotations

import bisect
import json
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Sequence

SegmentKind = Literal["land", "cold_sea", "warm_sea"]
RxArea = Literal["rural", "suburban", "urban", "dense_urban", "sea"]

IMPLEMENTATION = {
    "model": "ITU-R P.1546-6 (08/2019)",
    "implementation": "ific_guard.engines.p1546 1.0",
    "ported_from": "eeveetza/p1546 v6.2",
    "source_commit": "0e4263999ce055100f42257c2aafbac96ac630af",
    "source_doi": "10.5281/zenodo.6206015",
}

_TABLES_PATH = Path(__file__).with_name("p1546_tables.json")
_TIMES = (1.0, 10.0, 50.0)
_FREQUENCIES = (100.0, 600.0, 2000.0)
_FIGURE_OFFSET = {100.0: 0, 600.0: 8, 2000.0: 16}
_FIGURE_BASE = {
    (50.0, "land"): 1,
    (10.0, "land"): 2,
    (1.0, "land"): 3,
    (50.0, "cold_sea"): 4,
    (50.0, "warm_sea"): 4,
    (10.0, "cold_sea"): 5,
    (1.0, "cold_sea"): 6,
    (10.0, "warm_sea"): 7,
    (1.0, "warm_sea"): 8,
}
_LAND_AREAS = ("rural", "suburban", "urban", "dense_urban")
_DIFFRACTION_K = {100.0: 1.35, 600.0: 3.31, 2000.0: 6.0}


class P1546RangeError(ValueError):
    """An input is outside the range for which P.1546-6 is valid."""


@dataclass(frozen=True)
class PathSegment:
    """One stretch of the path, listed from the transmitter outwards."""

    length_km: float
    kind: SegmentKind


@dataclass(frozen=True)
class FieldStrengthResult:
    field_strength_dbuv_m: float
    """Predicted field strength at the notified power, dB(uV/m)."""
    basic_transmission_loss_db: float
    emax_dbuv_m: float
    """Maximum field strength for 1 kW, Annex 5 s.2."""
    h1_m: float
    """Transmitting antenna height actually used, Annex 5 s.3."""
    steps: dict[str, float]
    calc_inputs: dict
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...]
    implementation: dict[str, str]

    def to_details(self) -> dict:
        """Calculated keys for ``Case.details``. The caller adds limit and margin."""
        return {
            "method": "p1546",
            "calc_inputs": {**self.calc_inputs, "implementation": self.implementation},
            "assumptions": [*self.assumptions, *self.warnings],
            "value_db": self.field_strength_dbuv_m,
            "citations": [{"doc": "Rec. ITU-R P.1546-6 (08/2019)", "clause": "Annex 5, Annex 6"}],
        }


def field_strength(
    *,
    frequency_mhz: float,
    time_percent: float,
    tx_effective_height_m: float,
    rx_height_m: float,
    rx_clutter_height_m: float,
    segments: Sequence[PathSegment],
    erp_kw: float,
    rx_area: RxArea = "rural",
    tx_height_agl_m: float | None = None,
    tx_clutter_height_m: float | None = None,
) -> FieldStrengthResult:
    """Predict the field strength at the receiver.

    Args:
        frequency_mhz: 30 to 4000.
        time_percent: percentage of time exceeded, 1 to 50.
        tx_effective_height_m: heff (Annex 5 s.3): height above the average ground
            3-15 km towards the receiver; may be negative on land. For an all-sea
            path, the height above the sea surface. At most 3000.
        rx_height_m: h2 above ground. From 1 next to land or 3 next to sea, below 3000.
        rx_clutter_height_m: R2 around the receiver, not negative. For ``rural`` and
            ``sea`` receivers the Recommendation fixes the reference at 10 m, so
            other values have no effect and are noted in ``assumptions``.
        segments: land and sea stretches, 1000 km in total at most. Warm and cold
            sea on one path are treated as warm (Annex 5 s.8).
        erp_kw: e.r.p. in kW. The caller converts notified dBW or e.i.r.p. first.
        rx_area: surroundings of the receiver; ``sea`` means adjacent to sea.
        tx_height_agl_m: ha above ground, above 1. Required for a path with land
            under 15 km (equations 4-5) and for any path under 1 km. If omitted on
            longer paths, the slope-path correction (s.14) is skipped and noted.
        tx_clutter_height_m: R1 around the transmitter. If omitted, the transmitter
            is treated as open and the s.10 correction is skipped and noted.

    Raises:
        P1546RangeError: an input is outside the valid range or a required one is missing.
    """
    _check_finite(
        frequency_mhz=frequency_mhz,
        time_percent=time_percent,
        tx_effective_height_m=tx_effective_height_m,
        rx_height_m=rx_height_m,
        rx_clutter_height_m=rx_clutter_height_m,
        erp_kw=erp_kw,
        tx_height_agl_m=tx_height_agl_m,
        tx_clutter_height_m=tx_clutter_height_m,
    )
    _check_range("frequency_mhz", frequency_mhz, 30, 4000)
    _check_range("time_percent", time_percent, 1, 50)
    if erp_kw <= 0:
        raise P1546RangeError(f"erp_kw = {erp_kw} must be greater than 0")
    if rx_clutter_height_m < 0:
        raise P1546RangeError(f"rx_clutter_height_m = {rx_clutter_height_m} must not be negative")
    if tx_clutter_height_m is not None and tx_clutter_height_m < 0:
        raise P1546RangeError(f"tx_clutter_height_m = {tx_clutter_height_m} must not be negative")
    if tx_height_agl_m is not None and tx_height_agl_m <= 1:
        raise P1546RangeError(f"tx_height_agl_m = {tx_height_agl_m} must be greater than 1 m (Table 4)")
    if rx_area not in (*_LAND_AREAS, "sea"):
        raise P1546RangeError(f"rx_area = {rx_area!r} must be one of {(*_LAND_AREAS, 'sea')}")

    d_land, d_sea, sea_kind = _split_segments(segments)
    distance_km = d_land + d_sea
    if distance_km > 1000:
        raise P1546RangeError(f"path length {distance_km} km is greater than the 1000 km limit")

    min_h2 = 3 if rx_area == "sea" else 1
    if not min_h2 <= rx_height_m < 3000:
        raise P1546RangeError(
            f"rx_height_m = {rx_height_m} must be from {min_h2} m and below 3000 m when rx_area = {rx_area!r}"
        )
    if distance_km < 1 and tx_height_agl_m is None:
        raise P1546RangeError("tx_height_agl_m is required for paths under 1 km (Annex 5 s.15)")

    warnings: list[str] = []
    h1 = _determine_h1(distance_km, d_land, tx_effective_height_m, tx_height_agl_m, warnings)

    outcome = _predict(
        f=frequency_mhz,
        t=time_percent,
        h1=h1,
        h2=rx_height_m,
        r2=rx_clutter_height_m,
        area=rx_area,
        d_land=d_land,
        d_sea=d_sea,
        sea_kind=sea_kind,
        erp_kw=erp_kw,
        ha=tx_height_agl_m,
        r1=tx_clutter_height_m,
        warnings=warnings,
    )

    assumptions = ["50% of locations", "No terrain profile: terrain clearance and tropospheric scatter not applied"]
    if tx_height_agl_m is None:
        assumptions.append("tx_height_agl_m not given: slope-path correction (s.14) not applied")
    if tx_clutter_height_m is None:
        assumptions.append("tx_clutter_height_m not given: transmitter treated as open, s.10 correction not applied")
    if rx_area in ("rural", "sea") and rx_clutter_height_m != 10:
        assumptions.append(f"rx_area {rx_area}: reference clutter height fixed at 10 m (s.9), input not used")

    return FieldStrengthResult(
        field_strength_dbuv_m=outcome["e"],
        basic_transmission_loss_db=outcome["lb"],
        emax_dbuv_m=outcome["emax"],
        h1_m=h1,
        steps=outcome["steps"],
        calc_inputs={
            "frequency_mhz": frequency_mhz,
            "time_percent": time_percent,
            "tx_effective_height_m": tx_effective_height_m,
            "tx_height_agl_m": tx_height_agl_m,
            "tx_clutter_height_m": tx_clutter_height_m,
            "rx_height_m": rx_height_m,
            "rx_clutter_height_m": rx_clutter_height_m,
            "rx_area": rx_area,
            "segments": [asdict(s) for s in segments],
            "erp_kw": erp_kw,
        },
        assumptions=tuple(assumptions),
        warnings=tuple(warnings),
        implementation=dict(IMPLEMENTATION),
    )


def _check_finite(**values: float | None) -> None:
    for name, value in values.items():
        if value is not None and not math.isfinite(value):
            raise P1546RangeError(f"{name} = {value} is not a finite number")


def _check_range(name: str, value: float, low: float, high: float) -> None:
    if not low <= value <= high:
        raise P1546RangeError(f"{name} = {value} is outside the valid range {low} to {high}")


def _split_segments(segments: Sequence[PathSegment]) -> tuple[float, float, str]:
    if not segments:
        raise P1546RangeError("segments must not be empty")
    d_land = d_sea = 0.0
    kinds = set()
    for segment in segments:
        if not math.isfinite(segment.length_km) or segment.length_km <= 0:
            raise P1546RangeError(f"segment length {segment.length_km} km must be greater than 0")
        if segment.kind == "land":
            d_land += segment.length_km
        elif segment.kind in ("cold_sea", "warm_sea"):
            d_sea += segment.length_km
        else:
            raise P1546RangeError(f"segment kind {segment.kind!r} must be land, cold_sea or warm_sea")
        kinds.add(segment.kind)
    return d_land, d_sea, "warm_sea" if "warm_sea" in kinds else "cold_sea"


def _determine_h1(
    distance_km: float, d_land: float, heff: float, ha: float | None, warnings: list[str]
) -> float:
    """Annex 5 s.3, without terrain information. A mixed path counts as land."""
    if heff > 3000:
        raise P1546RangeError(f"tx_effective_height_m = {heff} is above the 3000 m limit (s.4.1)")
    if d_land == 0:
        if heff < 1:
            raise P1546RangeError(f"h1 = {heff} m on a sea path is below the 1 m limit (s.3.3)")
        if heff < 3:
            warnings.append(f"h1 = {heff} m on a sea path: not reliable below about 3 m (s.3.3)")
        return heff
    if distance_km >= 15:
        return heff
    if ha is None:
        raise P1546RangeError("tx_height_agl_m is required for land paths under 15 km (equations 4-5)")
    h1 = ha if distance_km <= 3 else ha + (heff - ha) * (distance_km - 3) / 12
    if h1 > 3000:
        raise P1546RangeError(f"h1 = {h1} m is above the 3000 m limit (s.4.1)")
    return h1


def _predict(
    *,
    f: float,
    t: float,
    h1: float,
    h2: float,
    r2: float,
    area: str,
    d_land: float,
    d_sea: float,
    sea_kind: str,
    erp_kw: float,
    ha: float | None = None,
    r1: float | None = None,
    tca: float | None = None,
    eff1: float | None = None,
    eff2: float | None = None,
    htter: float | None = None,
    hrter: float | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """The step-by-step method of Annex 6, with h1 already determined.

    The terrain arguments exist only so that the upstream validation profiles,
    which were logged with terrain information, can exercise every step.
    """
    warnings = warnings if warnings is not None else []
    d = d_land + d_sea
    d_steps = max(d, 1.0)  # Steps 1-16 use 1 km for shorter paths (s.15).
    has_slope = ha is not None
    steps: dict[str, float] = {}

    emax = _emax(t, d_land, d_sea)
    if has_slope:
        emax += _slope_correction(ha, h2, d, htter, hrter)

    mixed = d_land > 0 and d_sea > 0
    e_land = _field_for_type(h1, d_steps, "land", f, emax, t) if d_land > 0 else None
    h1_sea = 3.0 if mixed and h1 < 3 else h1  # s.8
    e_sea = _field_for_type(h1_sea, d_steps, sea_kind, f, emax, t) if d_sea > 0 else None
    e = _combine_mixed(e_land, e_sea, d_land, d_sea)
    steps["path_field_strength_dbuv_m"] = e

    if tca is not None:
        correction = _terrain_clearance_correction(f, tca)
        steps["terrain_clearance_correction_db"] = correction
        e += correction

    if eff1 is not None and eff2 is not None:
        ets = _troposcatter(d_steps, f, t, eff1, eff2)
        steps["troposcatter_field_strength_dbuv_m"] = ets
        e = max(e, ets)

    correction, r2_prime = _rx_height_correction(h1, d_steps, r2, h2, f, area, warnings)
    steps["rx_representative_clutter_m"] = r2_prime
    steps["rx_height_correction_db"] = correction
    e += correction

    if ha is not None and r1 is not None:
        correction = _tx_clutter_correction(ha, r1, f)
        steps["tx_clutter_correction_db"] = correction
        e += correction

    if has_slope:
        correction = _slope_correction(ha, h2, d_steps, htter, hrter)
        steps["slope_correction_db"] = correction
        e += correction

    if d < 0.9999999999:
        e = _short_path(ha, h2, d, e, htter, hrter)

    if e > emax:
        e = emax  # Step 19

    return {
        "e": e + 10 * math.log10(erp_kw),
        "lb": 139.3 - e + 20 * math.log10(f),
        "emax": emax,
        "steps": steps,
    }


@lru_cache(maxsize=1)
def _tables() -> dict:
    return json.loads(_TABLES_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _distance_index() -> dict[float, int]:
    return {distance: i for i, distance in enumerate(_tables()["distances_km"])}


@lru_cache(maxsize=1)
def _height_index() -> dict[float, int]:
    return {height: i for i, height in enumerate(_tables()["heights_m"])}


def _bracket(grid: Sequence[float], value: float) -> tuple[float, float]:
    """Nominal values around ``value``: equal if it is on the grid, the end pair outside it."""
    if value > grid[-1]:
        return grid[-2], grid[-1]
    if value < grid[0]:
        return grid[0], grid[1]
    i = bisect.bisect_left(grid, value)
    if grid[i] == value:
        return value, value
    return grid[i - 1], grid[i]


def _log_interp(x: float, x_inf: float, x_sup: float, y_inf: float, y_sup: float) -> float:
    return y_inf + (y_sup - y_inf) * math.log10(x / x_inf) / math.log10(x_sup / x_inf)


def _j(nu: float) -> float:
    """Equation 12a."""
    return 6.9 + 20 * math.log10(math.sqrt((nu - 0.1) ** 2 + 1) + nu - 0.1)


def _qi(x: float) -> float:
    """Annex 5 s.16, inverse complementary cumulative normal distribution."""

    def t(y: float) -> float:
        return math.sqrt(-2 * math.log(y))

    def c(z: float) -> float:
        tz = t(z)
        return ((0.010328 * tz + 0.802853) * tz + 2.515517) / (
            ((0.001308 * tz + 0.189269) * tz + 1.432788) * tz + 1
        )

    return t(x) - c(x)  # x <= 0.5 here: only time percentages of 1 to 50 reach this.


def _d06(f: float, h1: float, h2: float) -> float:
    """Annex 5 s.18, path length for 0.6 Fresnel clearance, km."""
    h1 = max(h1, 0.0)
    d_f = 0.0000389 * f * h1 * h2
    d_h = 4.1 * (math.sqrt(h1) + math.sqrt(h2))
    return max(d_f * d_h / (d_f + d_h), 0.001)


def _emax(t: float, d_land: float, d_sea: float) -> float:
    """Annex 5 s.2 and equation 42."""
    total = d_land + d_sea
    e_fs = 106.9 - 20 * math.log10(total)
    e_se = 2.38 * (1 - math.exp(-total / 8.94)) * math.log10(50 / t)
    return e_fs + d_sea * e_se / total


def _tabulated(figure: int, height: float, d: float) -> float:
    """Tabulated field strength at a nominal height, interpolated for distance (s.5)."""
    tables = _tables()
    rows = tables["figures"][str(figure)]
    column = _height_index()[height]
    d_inf, d_sup = _bracket(tables["distances_km"], d)
    index = _distance_index()
    e_inf = rows[index[d_inf]][column]
    if d_inf == d_sup:
        return e_inf
    return _log_interp(d, d_inf, d_sup, e_inf, rows[index[d_sup]][column])


def _field_for_type(h1: float, d: float, kind: str, f: float, emax: float, t: float) -> float:
    """Steps 6 to 10 for one propagation type."""
    t_inf, t_sup = _bracket(_TIMES, t)
    e_inf = _field_at_time(h1, d, kind, f, emax, t, t_inf)
    if t_inf == t_sup:
        return e_inf
    e_sup = _field_at_time(h1, d, kind, f, emax, t, t_sup)
    q_inf, q_sup, q_t = _qi(t_inf / 100), _qi(t_sup / 100), _qi(t / 100)
    return e_sup * (q_inf - q_t) / (q_inf - q_sup) + e_inf * (q_t - q_sup) / (q_inf - q_sup)  # (16)


def _field_at_time(h1: float, d: float, kind: str, f: float, emax: float, t: float, t_nom: float) -> float:
    if kind != "land" and f < 100:
        d600 = _d06(600, h1, 10)
        if d < d600:  # s.6, equations 15a and 15b
            df = _d06(f, h1, 10)
            if d <= df:
                return _emax(t, 0, d)
            e_df = _emax(t, 0, df)
            e_d600 = _field_at_frequency(h1, d600, kind, f, emax, t, t_nom)
            return _log_interp(d, df, d600, e_df, e_d600)
    return _field_at_frequency(h1, d, kind, f, emax, t, t_nom)


def _field_at_frequency(h1: float, d: float, kind: str, f: float, emax: float, t: float, t_nom: float) -> float:
    """Steps 7 to 9, s.6."""
    f_inf, f_sup = _bracket(_FREQUENCIES, f)
    e_inf = _field_at_nominal(h1, d, kind, f_inf, emax, t, t_nom)
    if f_inf == f_sup:
        return e_inf
    e_sup = _field_at_nominal(h1, d, kind, f_sup, emax, t, t_nom)
    e = _log_interp(f, f_inf, f_sup, e_inf, e_sup)
    return min(e, emax) if f > 2000 else e


def _field_at_nominal(h1: float, d: float, kind: str, f_nom: float, emax: float, t: float, t_nom: float) -> float:
    figure = _FIGURE_BASE[(t_nom, kind)] + _FIGURE_OFFSET[f_nom]
    if h1 >= 10:
        return min(_height_interp(figure, h1, d), emax)
    if kind == "land":
        return _low_h1_land(figure, h1, d, f_nom)
    return _low_h1_sea(figure, h1, d, f_nom, emax, t)


def _height_interp(figure: int, h1: float, d: float) -> float:
    """Annex 5 s.4.1, equation 8."""
    h_inf, h_sup = _bracket(_tables()["heights_m"], h1)
    e_inf = _tabulated(figure, h_inf, d)
    if h_inf == h_sup:
        return e_inf
    return _log_interp(h1, h_inf, h_sup, e_inf, _tabulated(figure, h_sup, d))


def _diffraction_correction(f_nom: float, h1: float) -> float:
    """Equations 12 to 12d."""
    nu = _DIFFRACTION_K[f_nom] * math.degrees(math.atan(-h1 / 9000))
    return 6.03 - (_j(nu) if nu > -0.7806 else 0.0)


def _e_zero(e10: float, e20: float, f_nom: float) -> float:
    """Equation 9a."""
    return e10 + 0.5 * (e10 - e20 + _diffraction_correction(f_nom, -10))


def _low_h1_land(figure: int, h1: float, d: float, f_nom: float) -> float:
    """Annex 5 s.4.2 for 0 <= h1 < 10 m, and s.4.3 case b for negative h1."""
    e10 = _tabulated(figure, 10, d)
    e20 = _tabulated(figure, 20, d)
    e_zero = _e_zero(e10, e20, f_nom)
    if h1 >= 0:
        return e_zero + 0.1 * h1 * (e10 - e_zero)
    return e_zero + _diffraction_correction(f_nom, h1)


def _low_h1_sea(figure: int, h1: float, d: float, f_nom: float, emax: float, t: float) -> float:
    """Annex 5 s.4.2, equations 10 and 11."""
    if h1 < 1:
        raise P1546RangeError(f"h1 = {h1} m on a sea path is below the 1 m limit (s.3.3)")
    d_h1 = _d06(f_nom, h1, 10)
    d_20 = _d06(f_nom, 20, 10)
    if d <= d_h1:
        return emax  # (11a)
    height_factor = math.log10(h1 / 10) / math.log10(2)
    if d < d_20:  # (11b)
        e10_d20 = _tabulated(figure, 10, d_20)
        e20_d20 = _tabulated(figure, 20, d_20)
        e_d20 = e10_d20 + (e20_d20 - e10_d20) * height_factor
        return _log_interp(d, d_h1, d_20, _emax(t, 0, d_h1), e_d20)
    e10 = _tabulated(figure, 10, d)  # (11c)
    e20 = _tabulated(figure, 20, d)
    e_prime = e10 + (e20 - e10) * height_factor
    e_zero = _e_zero(e10, e20, f_nom)
    e_double_prime = e_zero + 0.1 * h1 * (e10 - e_zero)
    f_s = (d - d_20) / d
    return e_prime * (1 - f_s) + e_double_prime * f_s


def _combine_mixed(e_land: float | None, e_sea: float | None, d_land: float, d_sea: float) -> float:
    """Annex 5 s.8, equations 17 to 20."""
    if e_sea is None:
        return e_land
    if e_land is None:
        return e_sea
    f_sea = d_sea / (d_land + d_sea)
    v = max(1.0, 1.0 + (e_sea - e_land) / 40)
    a = (1 - (1 - f_sea) ** (2 / 3)) ** v
    return (1 - a) * e_land + a * e_sea


def _rx_height_correction(
    h1: float, d: float, r2: float, h2: float, f: float, area: str, warnings: list[str]
) -> tuple[float, float]:
    """Annex 5 s.9. Returns the correction and the representative clutter height used."""
    k_h2 = 3.2 + 6.2 * math.log10(f)
    if area == "sea":
        c10 = k_h2 * math.log10(h2 / 10)
        if h2 >= 10:
            return c10, 10.0
        d10 = _d06(f, h1, 10)
        if d >= d10:
            return c10, 10.0
        d_h2 = _d06(f, h1, h2)
        if d <= d_h2:
            return 0.0, 10.0
        return c10 * math.log10(d / d_h2) / math.log10(d10 / d_h2), 10.0

    if area == "rural":
        return k_h2 * math.log10(h2 / 10), 10.0

    r_prime = (1000 * d * r2 - 15 * h1) / (1000 * d - 15)
    if r_prime < 1:
        r_prime = 1.0
        warnings.append("Modified representative clutter height limited to 1 m (s.9)")
    if h2 < r_prime:
        h_dif = r_prime - h2
        theta_clut = math.degrees(math.atan(h_dif / 27))
        nu = 0.0108 * math.sqrt(f) * math.sqrt(h_dif * theta_clut)
        correction = 6.03 - _j(nu)
    else:
        correction = k_h2 * math.log10(h2 / r_prime)
    if r_prime < 10:
        correction -= k_h2 * math.log10(10 / r_prime)
    return correction, r_prime


def _terrain_clearance_correction(f: float, tca: float) -> float:
    """Annex 5 s.11, with the angle limited to 0.55-40 degrees."""
    tca = min(max(tca, 0.55), 40.0)
    return _j(0.036 * math.sqrt(f)) - _j(0.065 * tca * math.sqrt(f))


def _troposcatter(d: float, f: float, t: float, eff1: float, eff2: float) -> float:
    """Annex 5 s.13."""
    theta_s = max(180 * d / math.pi / 4 * 3 / 6370 + eff1 + eff2, 0.0)
    return (
        24.4
        - 20 * math.log10(d)
        - 10 * theta_s
        - (5 * math.log10(f) - 2.5 * (math.log10(f) - 3.3) ** 2)
        + 0.15 * 325
        + 10.1 * (-math.log10(0.02 * t)) ** 0.7
    )


def _tx_clutter_correction(ha: float, r1: float, f: float) -> float:
    """Annex 5 s.10."""
    h_dif = ha - r1
    theta_clut = math.degrees(math.atan(h_dif / 27))
    magnitude = 0.0108 * math.sqrt(f) * math.sqrt(h_dif * theta_clut)
    nu = magnitude if r1 >= ha else -magnitude
    return -_j(nu) if nu > -0.7806 else 0.0


def _slope_distance(ha: float, h2: float, d: float, htter: float | None, hrter: float | None) -> float:
    """Equations 37a and 37b."""
    tx_terrain = 0.0 if htter is None else htter
    rx_terrain = 0.0 if hrter is None else hrter
    return math.sqrt(d * d + 1e-6 * ((ha + tx_terrain) - (h2 + rx_terrain)) ** 2)


def _slope_correction(ha: float, h2: float, d: float, htter: float | None, hrter: float | None) -> float:
    """Annex 5 s.14."""
    return 20 * math.log10(d / _slope_distance(ha, h2, d, htter, hrter))


def _short_path(ha: float, h2: float, d: float, e_1km: float, htter: float | None, hrter: float | None) -> float:
    """Annex 5 s.15, paths under 1 km."""
    d_slope = _slope_distance(ha, h2, d, htter, hrter)
    if d <= 0.04:
        return 106.9 - 20 * math.log10(d_slope)
    d_inf_slope = _slope_distance(ha, h2, 0.04, htter, hrter)
    d_sup_slope = _slope_distance(ha, h2, 1.0, htter, hrter)
    e_inf = 106.9 - 20 * math.log10(d_inf_slope)
    return e_inf + (e_1km - e_inf) * math.log10(d_slope / d_inf_slope) / math.log10(d_sup_slope / d_inf_slope)
