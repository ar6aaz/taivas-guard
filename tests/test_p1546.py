"""P.1546 engine: upstream validation logs, hand-checkable cases, range errors, masks."""

import csv
import json
import math
import time
from pathlib import Path

import pytest

from ific_guard.engines.p1546 import (
    IMPLEMENTATION,
    P1546RangeError,
    PathSegment,
    _predict,
    field_strength,
)

VALIDATION_DIR = Path(__file__).parent / "p1546_validation"
LOGS = sorted((VALIDATION_DIR / "results").glob("*_log.csv"))
MASKS_PATH = Path(__file__).parents[1] / "data" / "masks" / "field_strength.json"

# Final field strength and loss are logged to 8 decimals, but the inputs to only
# 6 significant figures, which sets the agreement we can expect. Observed maximum: 5.1e-5 dB.
FINAL_TOLERANCE_DB = 0.001
# Intermediate values are themselves logged to 6 significant figures.
STEP_TOLERANCE_DB = 0.002


def _number(text: str) -> float | None:
    text = text.strip()
    return float(text) if text else None


def _read_log(path: Path) -> dict[str, list[str]]:
    # Two upstream logs use an older format: BOM, semicolons and "See path".
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    text = text.replace(";", ",").replace("See path", "Sea path")
    return {row[0].strip(): row for row in csv.reader(text.splitlines()) if row}


def _terrain_heights(log_path: Path) -> tuple[float, float]:
    """Terrain height at the transmitter and receiver, which the logs do not record."""
    stem = log_path.name.removesuffix("_log.csv").rsplit("_", 1)[0]
    lines = (VALIDATION_DIR / "profiles" / f"{stem}.csv").read_text(encoding="latin-1").splitlines()
    first_point = next(line for line in lines if line.startswith("First Point")).split(",")[1].strip()
    start = next(i for i, line in enumerate(lines) if line.startswith("{Begin of Profile"))
    end = next(i for i, line in enumerate(lines) if line.startswith("{End of Profile"))
    points = lines[start + 2 : end]
    first, last = float(points[0].split(",")[1]), float(points[-1].split(",")[1])
    return (first, last) if first_point == "T" else (last, first)


def _run_logged_case(log_path: Path) -> tuple[dict, dict[str, list[str]]]:
    rows = _read_log(log_path)

    def value(key: str) -> float | None:
        return _number(rows[key][3])

    htter, hrter = _terrain_heights(log_path)
    tca = value("Terrain clearance angle tca (deg)")
    outcome = _predict(
        f=value("Frequency f (MHz)"),
        t=value("Percentage time t (%)"),
        h1=value("Tx antenna height h1 (m)"),
        h2=value("Rx antenna height a. g. h2 (m)"),
        r2=value("Rx clutter height R2 (m)"),
        area=rows["Rx clutter type"][3].strip().lower().replace(" ", "_"),
        d_land=value("Land path (km)"),
        d_sea=value("Sea path (km)"),
        sea_kind="warm_sea" if rows["Sea path (km)"][1].strip() == "Warm" else "cold_sea",
        erp_kw=value("Tx Power (kW)"),
        ha=value("Tx antenna height a. g. ha (m)"),
        r1=value("Tx clutter height R1 (m)"),
        tca=tca,
        eff1=value("Tx effective TCA  theta_eff1 (deg)"),
        eff2=tca,
        htter=htter,
        hrter=hrter,
    )
    return outcome, rows


def test_all_upstream_logs_are_present():
    assert len(LOGS) == 54
    assert len(list((VALIDATION_DIR / "profiles").glob("*.csv"))) == 24


@pytest.mark.parametrize("log_path", LOGS, ids=lambda path: path.stem)
def test_matches_upstream_validation_log(log_path):
    outcome, rows = _run_logged_case(log_path)

    def logged(key: str) -> float | None:
        return _number(rows[key][3])

    assert outcome["e"] == pytest.approx(
        logged("Resulting field strength for given PTx (dBuV/m)"), abs=FINAL_TOLERANCE_DB
    )
    assert outcome["lb"] == pytest.approx(
        logged("Resulting basic transmission loss (dB)"), abs=FINAL_TOLERANCE_DB
    )
    assert outcome["emax"] == pytest.approx(
        logged("Maximum field strength Emax (dBuV/m)"), abs=STEP_TOLERANCE_DB
    )
    steps = outcome["steps"]
    for step, key in {
        "path_field_strength_dbuv_m": "Field strength (dBuV/m)",
        "terrain_clearance_correction_db": "TCA correction (dB)",
        "troposcatter_field_strength_dbuv_m": "Trop. Scatt. field strength Ets (dBuV/m)",
        "rx_representative_clutter_m": "Rx repr. clutter height R2' (m)",
        "rx_height_correction_db": "Rx antenna height correction (dB)",
        "tx_clutter_correction_db": "Tx clutter correction (dB)",
        "slope_correction_db": "Rx slope-path correction (dB)",
    }.items():
        assert steps[step] == pytest.approx(logged(key), abs=STEP_TOLERANCE_DB), step


def _basic(**overrides):
    arguments = {
        "frequency_mhz": 100,
        "time_percent": 10,
        "tx_effective_height_m": 75,
        "rx_height_m": 10,
        "rx_clutter_height_m": 10,
        "segments": [PathSegment(50, "land")],
        "erp_kw": 1,
    }
    return field_strength(**{**arguments, **overrides})


def test_hand_check_land_path_reads_the_table():
    """Figure 2 (100 MHz, land, 10%), h1 = 75 m, 50 km: table value 38.8646.

    At h2 = 10 m in a rural area the receiver correction is zero.
    Loss: 139.3 - 38.8646 + 20 log10(100) = 140.4354 dB.
    """
    result = _basic()
    assert result.field_strength_dbuv_m == pytest.approx(38.8646, abs=1e-9)
    assert result.basic_transmission_loss_db == pytest.approx(140.4354, abs=1e-9)
    assert result.h1_m == 75


def test_hand_check_land_path_height_interpolation_and_power():
    """h1 = 100 m lies between the 75 m (38.8646) and 150 m (44.0024) curves, equation 8.

    log10(100/75) / log10(150/75) = 0.415037; 38.8646 + 5.1378 * 0.415037 = 40.99697.
    10 kW adds 10 dB to the field strength and leaves the loss unchanged.
    """
    result = _basic(tx_effective_height_m=100, erp_kw=10)
    assert result.field_strength_dbuv_m == pytest.approx(40.99697 + 10, abs=1e-4)
    assert result.basic_transmission_loss_db == pytest.approx(139.3 - 40.99697 + 40, abs=1e-4)


def test_hand_check_sea_path_reads_the_table():
    """Figure 5 (100 MHz, cold sea, 10%), h1 = 75 m, 50 km: table value 45.7911."""
    result = _basic(segments=[PathSegment(50, "cold_sea")], rx_area="sea")
    assert result.field_strength_dbuv_m == pytest.approx(45.7911, abs=1e-9)


def test_emax_on_sea_path_follows_equations_2_and_3():
    """Emax = 106.9 - 20 log10(50) + 2.38 (1 - exp(-50/8.94)) log10(50/10)."""
    expected = 106.9 - 20 * math.log10(50) + 2.38 * (1 - math.exp(-50 / 8.94)) * math.log10(5)
    result = _basic(segments=[PathSegment(50, "cold_sea")], rx_area="sea")
    assert result.emax_dbuv_m == pytest.approx(expected, abs=1e-9)


def test_hand_check_mixed_path_follows_equations_17_to_20():
    """25 km land then 25 km cold sea, using the two table values above."""
    e_land, e_sea = 38.8646, 45.7911
    a0 = 1 - 0.5 ** (2 / 3)
    v = 1 + (e_sea - e_land) / 40
    a = a0**v
    expected = (1 - a) * e_land + a * e_sea
    result = _basic(segments=[PathSegment(25, "land"), PathSegment(25, "cold_sea")])
    assert result.field_strength_dbuv_m == pytest.approx(expected, abs=1e-9)


def test_low_sea_antenna_uses_same_log_base_as_the_recommendation():
    """Annex 5 s.4.2, equation 11c: h1 = 5 m, 100 MHz, 50 km, E10 = 31.4279, E20 = 35.7233.

    E' uses log(h1/10)/log(20/10) = -1 exactly. Upstream line 1470 divides by the
    natural log of 2 instead, which would give a higher E' by about 2.4 dB.
    """
    e10, e20 = 31.4279, 35.7233
    e_prime = e10 + (e20 - e10) * -1
    theta = math.degrees(math.atan(10 / 9000))
    nu = 1.35 * theta
    j = 6.9 + 20 * math.log10(math.sqrt((nu - 0.1) ** 2 + 1) + nu - 0.1)
    e_zero = e10 + 0.5 * ((e10 - e20) + (6.03 - j))
    e_double_prime = e_zero + 0.1 * 5 * (e10 - e_zero)
    d_f = 0.0000389 * 100 * 20 * 10
    d_h = 4.1 * (math.sqrt(20) + math.sqrt(10))
    d20 = d_f * d_h / (d_f + d_h)
    f_s = (50 - d20) / 50
    expected = e_prime * (1 - f_s) + e_double_prime * f_s

    result = _basic(tx_effective_height_m=5, segments=[PathSegment(50, "cold_sea")], rx_area="sea")
    assert result.field_strength_dbuv_m == pytest.approx(expected, abs=1e-9)


def test_mixed_path_with_low_antenna_uses_3_m_for_the_sea_part():
    """Annex 5 s.8: h1 < 3 m stays for Eland, but Esea uses 3 m."""
    e_land = _basic(tx_effective_height_m=2).field_strength_dbuv_m
    e_sea = _basic(tx_effective_height_m=3, segments=[PathSegment(50, "cold_sea")]).field_strength_dbuv_m
    a = (1 - 0.5 ** (2 / 3)) ** max(1.0, 1 + (e_sea - e_land) / 40)
    mixed = _basic(tx_effective_height_m=2, segments=[PathSegment(25, "land"), PathSegment(25, "cold_sea")])
    assert mixed.field_strength_dbuv_m == pytest.approx((1 - a) * e_land + a * e_sea, abs=1e-9)


def _emax_sea(distance_km: float, time_percent: float) -> float:
    """Equations 2 and 3, written out independently of the engine."""
    return (
        106.9
        - 20 * math.log10(distance_km)
        + 2.38 * (1 - math.exp(-distance_km / 8.94)) * math.log10(50 / time_percent)
    )


def _d06_by_hand(f: float, h1: float, h2: float) -> float:
    """Annex 5 s.18, written out independently of the engine."""
    d_f = 0.0000389 * f * h1 * h2
    d_h = 4.1 * (math.sqrt(h1) + math.sqrt(h2))
    return d_f * d_h / (d_f + d_h)


def _log_interp_by_hand(x, x1, x2, y1, y2):
    return y1 + (y2 - y1) * math.log10(x / x1) / math.log10(x2 / x1)


# The upstream profiles do not reach equations 11a, 11b, 15a, 15b or the warm-sea
# figures, so these hand calculations are the only check on those branches.
def test_sea_below_100_mhz_within_fresnel_distance_is_emax():
    """Equation 15a: 50 MHz, h1 = 75 m. df is about 1.42 km, so 1 km is inside it."""
    assert _d06_by_hand(50, 75, 10) > 1
    result = _basic(frequency_mhz=50, segments=[PathSegment(1, "cold_sea")], rx_area="sea")
    assert result.field_strength_dbuv_m == pytest.approx(_emax_sea(1, 10), abs=1e-9)


def test_sea_below_100_mhz_between_fresnel_distances():
    """Equation 15b: 50 MHz, h1 = 75 m, 5 km, between df and d600 (about 12.9 km).

    Figures 5 (100 MHz) and 13 (600 MHz) at 12 and 13 km give E at d600, extrapolated
    to 50 MHz by equation 14.
    """
    df = _d06_by_hand(50, 75, 10)
    d600 = _d06_by_hand(600, 75, 10)
    assert df < 5 < d600 and 12 < d600 < 13
    e100 = _log_interp_by_hand(d600, 12, 13, 73.5474, 72.1216)
    e600 = _log_interp_by_hand(d600, 12, 13, 85.6621, 84.6272)
    e_d600 = _log_interp_by_hand(50, 100, 600, e100, e600)
    expected = _log_interp_by_hand(5, df, d600, _emax_sea(df, 10), e_d600)
    result = _basic(frequency_mhz=50, segments=[PathSegment(5, "cold_sea")], rx_area="sea")
    assert result.field_strength_dbuv_m == pytest.approx(expected, abs=1e-9)


def test_low_sea_antenna_within_fresnel_distance_is_emax():
    """Equation 11a: 600 MHz, h1 = 5 m. Dh1 is about 1.1 km, so 1 km is inside it."""
    assert _d06_by_hand(600, 5, 10) > 1
    result = _basic(
        frequency_mhz=600, tx_effective_height_m=5, segments=[PathSegment(1, "cold_sea")], rx_area="sea"
    )
    assert result.field_strength_dbuv_m == pytest.approx(_emax_sea(1, 10), abs=1e-9)


def test_low_sea_antenna_between_fresnel_distances():
    """Equation 11b: 600 MHz, h1 = 5 m, 2 km, between Dh1 and D20 (about 4.06 km).

    Figure 13 at 4 and 5 km gives E10 and E20 at D20, and log(5/10)/log(20/10) = -1.
    """
    d_h1 = _d06_by_hand(600, 5, 10)
    d20 = _d06_by_hand(600, 20, 10)
    assert d_h1 < 2 < d20 and 4 < d20 < 5
    e10 = _log_interp_by_hand(d20, 4, 5, 89.7915, 86.0149)
    e20 = _log_interp_by_hand(d20, 4, 5, 93.8856, 90.6419)
    e_d20 = e10 - (e20 - e10)
    expected = _log_interp_by_hand(2, d_h1, d20, _emax_sea(d_h1, 10), e_d20)
    result = _basic(
        frequency_mhz=600, tx_effective_height_m=5, segments=[PathSegment(2, "cold_sea")], rx_area="sea"
    )
    assert result.field_strength_dbuv_m == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize(
    ("time_percent", "kind", "figure_value"),
    [
        (50, "land", 36.2563),  # Figure 1
        (10, "land", 38.8646),  # Figure 2
        (1, "land", 42.1169),  # Figure 3
        (50, "cold_sea", 43.8318),  # Figure 4
        (50, "warm_sea", 43.8318),  # Figure 4 serves both
        (10, "cold_sea", 45.7911),  # Figure 5
        (1, "cold_sea", 50.6241),  # Figure 6
        (10, "warm_sea", 46.1286),  # Figure 7
        (1, "warm_sea", 51.8601),  # Figure 8
    ],
)
def test_path_type_and_time_select_the_right_figure(time_percent, kind, figure_value):
    """100 MHz, h1 = 75 m, 50 km."""
    result = _basic(time_percent=time_percent, segments=[PathSegment(50, kind)])
    assert result.field_strength_dbuv_m == pytest.approx(figure_value, abs=1e-9)


def test_height_correction_matches_the_figure_published_in_tr_25_08():
    """T/R 25-08 (6 June 2025), p.10: P.1546 gives 10.3 dB from 10 m to 3 m in the 400 MHz range."""
    at_10_m = _basic(frequency_mhz=450, rx_height_m=10).field_strength_dbuv_m
    at_3_m = _basic(frequency_mhz=450, rx_height_m=3).field_strength_dbuv_m
    assert round(at_10_m - at_3_m, 1) == 10.3


def test_warm_and_cold_sea_on_one_path_are_treated_as_warm():
    both = _basic(segments=[PathSegment(25, "warm_sea"), PathSegment(25, "cold_sea")])
    warm = _basic(segments=[PathSegment(50, "warm_sea")])
    assert both.field_strength_dbuv_m == warm.field_strength_dbuv_m


@pytest.mark.parametrize(
    ("distance_km", "heff", "ha", "h1"),
    [(2, 80, 20, 20), (3, 80, 20, 20), (9, 80, 20, 50), (15, 80, 20, 80), (40, 80, 20, 80)],
)
def test_h1_follows_equations_4_to_7(distance_km, heff, ha, h1):
    result = _basic(tx_effective_height_m=heff, tx_height_agl_m=ha, segments=[PathSegment(distance_km, "land")])
    assert result.h1_m == pytest.approx(h1)


def test_low_all_sea_antenna_warns_and_is_not_raised_to_3_m():
    result = _basic(tx_effective_height_m=2, segments=[PathSegment(50, "cold_sea")])
    assert result.h1_m == 2
    assert any("not reliable" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"frequency_mhz": 29.9}, "frequency_mhz"),
        ({"frequency_mhz": 4000.1}, "frequency_mhz"),
        ({"time_percent": 0.9}, "time_percent"),
        ({"time_percent": 50.1}, "time_percent"),
        ({"segments": [PathSegment(1000.1, "land")]}, "1000 km"),
        ({"segments": [PathSegment(600, "land"), PathSegment(401, "cold_sea")]}, "1000 km"),
        ({"segments": []}, "empty"),
        ({"segments": [PathSegment(0, "land")]}, "greater than 0"),
        ({"segments": [PathSegment(-5, "land")]}, "greater than 0"),
        ({"segments": [PathSegment(float("nan"), "land")]}, "greater than 0"),
        ({"segments": [PathSegment(10, "lake")]}, "segment kind"),
        ({"tx_effective_height_m": 3000.1}, "3000 m"),
        ({"tx_effective_height_m": 0.9, "segments": [PathSegment(50, "cold_sea")]}, "1 m limit"),
        ({"rx_height_m": 0.9}, "rx_height_m"),
        ({"rx_height_m": 3000}, "rx_height_m"),
        ({"rx_height_m": 2.9, "rx_area": "sea"}, "rx_height_m"),
        ({"erp_kw": 0}, "erp_kw"),
        ({"erp_kw": float("inf")}, "finite"),
        ({"frequency_mhz": float("nan")}, "finite"),
        ({"rx_area": "forest"}, "rx_area"),
        ({"rx_clutter_height_m": -1}, "rx_clutter_height_m"),
        ({"tx_clutter_height_m": -1}, "tx_clutter_height_m"),
        ({"tx_height_agl_m": 1}, "tx_height_agl_m"),
        ({"segments": [PathSegment(10, "land")]}, "tx_height_agl_m is required"),
        ({"segments": [PathSegment(0.5, "cold_sea")]}, "tx_height_agl_m is required"),
    ],
)
def test_out_of_range_input_raises(overrides, message):
    with pytest.raises(P1546RangeError, match=message):
        _basic(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"frequency_mhz": 30},
        {"frequency_mhz": 4000},
        {"time_percent": 1},
        {"time_percent": 50},
        {"segments": [PathSegment(1000, "land")]},
        {"tx_effective_height_m": 3000},
        {"tx_effective_height_m": -50},
        {"tx_effective_height_m": 1, "segments": [PathSegment(50, "cold_sea")]},
        {"rx_height_m": 1},
        {"rx_height_m": 3, "rx_area": "sea"},
    ],
)
def test_range_limits_themselves_are_accepted(overrides):
    assert math.isfinite(_basic(**overrides).field_strength_dbuv_m)


def test_output_records_version_inputs_and_assumptions():
    result = _basic()
    assert result.implementation == IMPLEMENTATION
    assert result.implementation["source_commit"].startswith("0e42639")
    assert result.calc_inputs["segments"] == [{"length_km": 50, "kind": "land"}]
    assert any("slope-path" in assumption for assumption in result.assumptions)

    details = result.to_details()
    assert details["method"] == "p1546"
    assert details["value_db"] == result.field_strength_dbuv_m
    assert details["calc_inputs"]["implementation"]["model"] == "ITU-R P.1546-6 (08/2019)"
    json.dumps(details)


def test_identical_inputs_give_identical_results():
    assert _basic() == _basic()


def test_single_call_is_fast_enough_for_batch_use():
    start = time.perf_counter()
    for _ in range(200):
        _basic(frequency_mhz=450, tx_effective_height_m=60)
    assert (time.perf_counter() - start) / 200 < 0.005


@pytest.fixture(scope="module")
def masks_file() -> dict:
    return json.loads(MASKS_PATH.read_text(encoding="utf-8"))


def test_masks_are_real_and_every_value_is_cited(masks_file):
    assert masks_file["test_only"] is False
    assert masks_file["source"]["edition"] == "6 June 2025"
    assert len(masks_file["source"]["pdf_sha256"]) == 64
    ids = [mask["mask_id"] for mask in masks_file["masks"]]
    assert len(ids) == len(set(ids))
    for mask in masks_file["masks"]:
        assert mask["kind"] == "field_strength"
        assert mask["unit"] == "dB(uV/m)"
        assert mask["f_low_mhz"] < mask["f_high_mhz"] <= 470
        assert mask["breakpoints"]
        citation = mask["citation"]
        assert citation["doc"].startswith("ECC Recommendation T/R 25-08")
        assert citation["clause"]
        assert citation["page_or_url"].startswith("p.")


def test_annex_1_thresholds_match_the_document(masks_file):
    """T/R 25-08 (6 June 2025), Annex 1, A1.2.3, p.6."""
    annex_1 = {
        (mask["f_low_mhz"], mask["f_high_mhz"]): mask["breakpoints"]
        for mask in masks_file["masks"]
        if mask["annex"] == 1
    }
    assert annex_1 == {
        (29.7, 47): [[0, 0]],
        (47, 108): [[0, 6]],
        (108, 380): [[0, 12]],
        (380, 400): [[0, 18]],
        (400, 470): [[0, 20]],
    }
    for mask in masks_file["masks"]:
        if mask["annex"] == 1:
            assert (mask["time_percent"], mask["location_percent"]) == (10, 50)
            assert (mask["rx_height_m"], mask["ref_bw_hz"]) == (10, 25000)


def test_every_neighbour_is_recorded_without_invented_border_values(masks_file):
    borders = {border["adm"]: border for border in masks_file["borders"]}
    assert set(borders) == {"RUS", "S", "EST", "NOR", "DNK"}
    for border in borders.values():
        assert border["border_specific_values_in_document"] is False
        assert border["citation"]["page_or_url"].startswith("p.")
    assert borders["RUS"]["annex_5_country_type"] is None
    assert borders["S"]["annex_5_country_type"] == 3
    assert borders["EST"]["annex_5_country_type"] == 2
    assert borders["NOR"]["annex_5_country_type"] == 4
    assert borders["DNK"]["annex_5_country_type"] == 1
