"""PFD rule: joins, non-GSO geometry, data-quality items, and hand calculations from real filings.

Expected numbers come from the closed forms below worked separately, not from the rule's output:
  d(h, a)   = sqrt((Re+h)^2 - (Re cos a)^2) - Re sin a,  Re = 6378 km
  spread(d) = 10 log10(4 pi (1000 d)^2)
  margin    = P + 10 log10(B_ref) + G - spread(d) - limit(a)
"""
import math
from pathlib import Path

import pytest

from ific_guard.config import get_settings
from ific_guard.engines.pfd import GroundPoint, finland_points, load_masks
from ific_guard.engines.pfd_ngso import envelope, propagate
from ific_guard.screening.rule_pfd import PfdRule, pfd_summary
from ific_guard.screening.scoring import score_finding
from ific_guard.screening.space import MdbReader, SpaceIssue
from tests.test_screening import FakeReader, freq, notice

SHARED_3074 = Path("/Users/mehrshad/personal/radio/data/shared/ific3074.mdb")
GROUP = {"grp_id": 1, "ntc_id": 1, "beam_name": "1", "emi_rcp": "E"}
BEAM_30 = {"ntc_id": 1, "emi_rcp": "E", "beam_name": "1", "gain": 30}


def run(reader) -> list:
    return list(PfdRule(get_settings()).run(SpaceIssue(reader)))


def gso_reader(lon=25.0, low=8025, high=8040, power=-60.0, pep=20.0, geo=True, **tables):
    return FakeReader(
        notice=[notice(1)],
        geo=[{"ntc_id": 1, "long_nom": lon}] if geo else [],
        freq=[freq(1, low, high, grp=1)],
        grp=[GROUP],
        emiss=[{"grp_id": 1, "seq_no": 1, "pwr_ds_max": power, "pep_max": pep}],
        s_beam=[BEAM_30],
        **tables,
    )


def ngso_reader(orbits, low=8025, high=8040, power=-60.0, ref_body="T", **tables):
    rows = [{"ntc_id": 1, "orb_id": i, "nbr_sat_pl": 1, **row} for i, row in enumerate(orbits, 1)]
    return FakeReader(
        notice=[notice(1, ntc_type="N")],
        non_geo=[{"ntc_id": 1, "sat_name": "TEST", "ref_body": ref_body}],
        freq=[freq(1, low, high, grp=1)],
        grp=[GROUP],
        emiss=[{"grp_id": 1, "seq_no": 1, "pwr_ds_max": power, "pep_max": 20.0}],
        s_beam=[BEAM_30],
        orbit=rows,
        **tables,
    )


def only_calc(hits):
    calcs = [c for hit in hits for c in hit.details["calculations"]]
    assert len(calcs) == 1
    return calcs[0]


def test_missing_power_and_gain_are_reported():
    reader = gso_reader(power=None, pep=None)
    reader.tables["s_beam"] = [{"ntc_id": 1, "emi_rcp": "E", "beam_name": "1", "gain": None}]
    hits = run(reader)
    assert hits and hits[0].details["method"] == "screening"
    assert hits[0].details["missing"][0]["missing"] == ["pwr_ds_max", "gain"]
    assert hits[0].details["unevaluated_by_reason"] == {"power_or_gain_missing": 1}


def test_rule_uses_maximum_joined_power_and_gain_and_is_repeatable():
    reader = gso_reader()
    reader.tables["emiss"] = [{"grp_id": 1, "pwr_ds_max": -50}, {"grp_id": 1, "pwr_ds_max": -40}]
    reader.tables["s_beam"] = [BEAM_30, {**BEAM_30, "gain": 20}]
    rule = PfdRule(get_settings())
    first, second = list(rule.run(SpaceIssue(reader))), list(rule.run(SpaceIssue(reader)))
    assert first[0].details == second[0].details
    calculated = [c for c in first[0].details["calculations"] if c["mask_id"] == "art21-8025-8500"]
    assert calculated and calculated[0]["calc_inputs"]["p_density_dbw_hz"] == -40
    assert calculated[0]["calc_inputs"]["gain_dbi"] == 30


def test_uncomputed_hit_is_not_presented_as_within_limit():
    reader = FakeReader(notice=[notice(1, ntc_type="N")], freq=[freq(1, 17700, 17800)], orbit=[])
    hit = next(iter(run(reader)))
    assert hit.details["method"] == "screening"
    assert hit.details["expert_review"] is True
    assert "not evaluated" in hit.title
    assert "within" not in hit.title
    assert hit.details["unevaluated_by_reason"] == {"orbit_missing": 1}


def test_filing_without_an_orbit_outside_masked_bands_is_not_a_pfd_hit():
    assert run(FakeReader(notice=[notice(1, ntc_type="N")], freq=[freq(1, 435, 438)], orbit=[])) == []


# --- Non-GSO envelope


def test_eccentric_orbit_is_evaluated_at_its_perigee_not_a_mean_altitude():
    # P -60, B_ref 4 kHz, G 30. Overhead at 500 km: -60 + 36.0206 + 30 - 124.9715 + 140 = +21.05.
    # A circular orbit at the 20,250 km mean altitude would peak at -3.03 dB, and would say below.
    hits = run(ngso_reader([{"perig_km": 500, "apog_km": 40000, "inclin_ang": 70}]))
    calc = only_calc(hits)
    assert calc["method"] == "pfd_ngso"
    assert calc["envelope"]["margin_db"] == pytest.approx(21.0491, abs=1e-3)
    assert calc["envelope"]["angle_deg"] == pytest.approx(90)
    assert calc["envelope"]["distance_km"] == pytest.approx(500)
    assert calc["envelope"]["altitude_km"] == pytest.approx(500)
    assert calc["worst_point"]["angle_deg"] == pytest.approx(90)


def test_angle_consistency_old_impossible_pairing_would_say_exceeds():
    # P -86, G 30: perigee distance with the low-angle limit gives -19.98 - 124.97 + 150 = +5.05, which
    # no satellite can reach. At a real pair the best is overhead: -19.98 - 124.97 + 140 = -4.95.
    # Spot checks at 500 km: 0 deg -9.19, 5 deg -7.32, 25 deg -11.24.
    hits = run(ngso_reader([{"perig_km": 500, "apog_km": 500, "inclin_ang": 70}], power=-86.0))
    calc = only_calc(hits)
    assert calc["result"] == "envelope_below"
    assert calc["margin_db"] == pytest.approx(-4.9509, abs=1e-3)
    assert calc["worst_point"]["angle_deg"] == pytest.approx(90)
    assert hits[0].severity == "low"
    assert "exceed" not in hits[0].title
    assert "not a compliance finding" in hits[0].title
    assert "below the limit" in hits[0].title


def test_low_inclination_orbit_that_cannot_reach_finland_is_a_stated_result():
    # i 30 from 59.816 N leaves 29.816 degrees of central angle. A 600 km horizon is only 23.93.
    for inclination in (30, 150):  # the retrograde supplement reaches the same latitude
        hits = run(ngso_reader([{"perig_km": 600, "apog_km": 600, "inclin_ang": inclination}]))
        assert len(hits) == 1
        hit = hits[0]
        assert hit.details["calculations"] == [] and hit.details["missing"] == []
        assert hit.details["not_visible_count"] == 1
        assert hit.details["not_visible"][0]["reason"] == "orbit_cannot_reach_finland"
        assert hit.severity == "low" and hit.details["concern"] is False
        assert "not visible from Finland" in hit.title and "missing" not in hit.title


def test_low_inclination_eccentric_orbit_climbs_to_see_finland():
    # Same latitude gap: seeing Finland needs Re / cos(29.816 deg) - Re = 973.09 km, well above perigee.
    # At the horizon, d = 3655.08 km and the limit is -150: -60 + 36.0206 + 30 - 141.2501 + 150 = +13.77.
    hits = run(ngso_reader([{"perig_km": 500, "apog_km": 40000, "inclin_ang": 30}]))
    block = only_calc(hits)["envelope"]
    assert block["altitude_km"] == pytest.approx(973.09, abs=0.01)
    assert block["distance_km"] == pytest.approx(3655.08, abs=0.01)
    assert block["margin_db"] == pytest.approx(13.7706, abs=1e-3)
    assert block["angle_deg"] == pytest.approx(0)


def test_envelope_step_stays_within_a_finer_search():
    mask = load_masks().by_id("art21-17700-19300-heo")
    best = envelope(300.0, 70000.0, 39.0, mask, 59.81602)
    psi = math.radians(59.81602 - 39.0)
    fine = -math.inf
    for i in range(90001):
        angle = i / 1000
        a = math.radians(angle)
        if a + psi >= math.pi / 2:
            break
        radius = max(6678.0, 6378.0 * math.cos(a) / math.cos(a + psi))
        if radius > 76378.0:
            break
        distance = math.sqrt(radius**2 - (6378.0 * math.cos(a)) ** 2) - 6378.0 * math.sin(a)
        fine = max(fine, -10 * math.log10(4 * math.pi * (distance * 1000) ** 2) - mask.limit_db(angle))
    assert fine - 0.02 <= best.relative_margin_db <= fine + 1e-9


def test_every_mask_limit_rises_or_stays_level_with_angle():
    # The envelope and the propagation frontier both rely on this.
    for mask in load_masks().masks:
        limits = [limit for _, limit in mask.breakpoints]
        assert limits == sorted(limits), mask.mask_id


# --- Non-GSO propagation


def test_propagation_starts_where_the_elements_put_it():
    # Polar and circular, phase 65 from a node at 25 E: at minute 0 it is directly over 65 N, 25 E.
    flight = propagate(1000.0, 1000.0, 90.0, 0.0, 25.0, 65.0, (GroundPoint(65.0, 25.0),))
    overhead = min(flight.frontier, key=lambda s: s.distance_km)
    assert overhead.minute == 0
    assert overhead.distance_km == pytest.approx(1000.0, abs=1e-6)
    assert overhead.angle_deg == pytest.approx(90.0, abs=1e-3)


def test_argument_of_perigee_decides_the_height_over_finland():
    # Molniya shape, a 12-hour orbit. With apogee north, the perigee stays deep in the south, so
    # Finland only sees it from thousands of km up. With perigee north, Finland sees it near perigee.
    point = (GroundPoint(65.0, 25.0),)
    apogee_north = propagate(500.0, 39000.0, 63.4, 270.0, 25.0, 90.0, point)
    perigee_north = propagate(500.0, 39000.0, 63.4, 90.0, 25.0, 90.0, point)
    assert min(s.altitude_km for s in apogee_north.frontier) > 8000
    assert min(s.altitude_km for s in perigee_north.frontier) < 1500


def test_propagated_exceedance_is_high_and_says_what_it_is():
    # A polar 1000 km orbit from 65 N over Finland. Overhead: -60 + 36.0206 + 30 - 130.9921 + 140 = +15.03.
    orbit = {"perig_km": 1000, "apog_km": 1000, "inclin_ang": 90, "perig_arg": 0, "long_asc": 25.0}
    hits = run(ngso_reader([orbit], phase=[{"ntc_id": 1, "orb_id": 1, "orb_sat_id": 1, "phase_ang": 65.0}]))
    calc = only_calc(hits)
    assert calc["result"] == "exceeds" and calc["margin_basis"] == "propagated_sample"
    assert calc["propagation"]["minutes"] == 1440 and calc["propagation"]["step_minutes"] == 1
    assert calc["propagation"]["satellites_flown"] == 1
    assert 0 < calc["margin_db"] <= calc["envelope"]["margin_db"] + 1e-9
    assert calc["envelope"]["margin_db"] == pytest.approx(15.0285, abs=1e-3)
    assert {"lat", "lon", "minute"} <= set(calc["worst_point"])
    assert hits[0].severity == "high"
    assert "exceeds in a one-day propagation under worst-case assumptions" in hits[0].title


def test_envelope_above_limit_without_propagation_is_not_called_exceeds():
    hits = run(ngso_reader([{"perig_km": 500, "apog_km": 40000, "inclin_ang": 70}]))
    calc = only_calc(hits)
    assert calc["result"] == "envelope_exceeds" and calc["margin_basis"] == "envelope"
    assert calc["propagation"]["kind"] == "not_run"
    assert "minutes" not in calc["propagation"]
    assert "located" in calc["worst_point"] and "lat" not in calc["worst_point"]
    assert hits[0].severity == "medium"
    assert "exceeds" not in hits[0].title and "upper bound above the limit; no time-sampled result" in hits[0].title


def test_envelope_margin_scores_less_than_a_sampled_one():
    sampled = score_finding({"margin_db": 2.0, "margin_basis": "propagated_sample"})
    bound = score_finding({"margin_db": 2.0, "margin_basis": "envelope"})
    assert sampled["score"] == 20.0 and bound["score"] == 10.0
    assert any("upper bound" in reason for reason in bound["reasons"])


def test_mask_rows_follow_the_orbit_shape():
    # 11 GHz: a low orbit takes the ordinary row, and a highly inclined, high-apogee orbit its own.
    low = run(ngso_reader([{"perig_km": 600, "apog_km": 600, "inclin_ang": 60}], low=11000, high=11010))
    assert [c["mask_id"] for c in low[0].details["calculations"]] == ["art21-10700-11700-ngso"]
    high = run(ngso_reader([{"perig_km": 1000, "apog_km": 40000, "inclin_ang": 63.4}], low=11000, high=11010))
    assert [c["mask_id"] for c in high[0].details["calculations"]] == ["art21-10700-12500-ngso-heo"]


def test_orbit_link_limits_a_beam_to_its_orbit_rows():
    orbits = [{"perig_km": 500, "apog_km": 500, "inclin_ang": 70}, {"perig_km": 2000, "apog_km": 2000, "inclin_ang": 70}]
    link = [{"ntc_id": 1, "orb_id": 2, "emi_rcp": "E", "beam_name": "1"}]
    calc = only_calc(run(ngso_reader(orbits, orbit_lnk=link)))
    assert [s["perigee_km"] for s in calc["calc_inputs"]["orbit_shapes"]] == [2000]


def test_mask_parameters_x_and_y_stay_counted_as_unevaluated():
    orbit = [{"perig_km": 600, "apog_km": 600, "inclin_ang": 60}]
    hits = run(ngso_reader(orbit, low=18000, high=18010))
    assert hits[0].details["unevaluated_by_reason"] == {"mask_parameter_not_evaluated": 1}
    assert [row["missing"] for row in hits[0].details["missing"]] == [["X"]]
    y = run(ngso_reader(orbit, low=3700, high=3710))
    assert [row["missing"] for row in y[0].details["missing"]] == [["Y"]]
    assert y[0].details["calculations"] == []
    assert "not evaluated; missing Y" in y[0].title
    assert pfd_summary(y)["unevaluated"] == {("ngso", "art21", "mask_parameter_not_evaluated", "Y"): 1}


def test_orbit_about_another_body_is_not_reported_as_missing_data_about_earth():
    reader = ngso_reader([], ref_body="L")
    hits = run(reader)
    assert hits[0].details["unevaluated_by_reason"] == {"orbit_not_earth_centred": 1}
    assert hits[0].details["missing"][0]["ref_body"] == "L"


# --- Implausible inputs


def test_power_density_above_total_power_is_a_data_quality_item_not_a_finding():
    # The shape of the 3074 row: 73 dBW/Hz with a total of -4 dBW. It is set aside, leaving -74.
    # At 500 km overhead, -74 + 36.0206 + 30 - 124.9715 + 140 = +7.05, the row's real margin.
    reader = ngso_reader([{"perig_km": 500, "apog_km": 500, "inclin_ang": 70}])
    reader.tables["emiss"] = [
        {"grp_id": 1, "seq_no": 1, "pwr_ds_max": -74.0, "pep_max": -4.0},
        {"grp_id": 1, "seq_no": 2, "pwr_ds_max": 73.0, "pep_max": -4.0, "design_emi": "8M00G2D--"},
    ]
    hits = run(reader)
    calc = only_calc(hits)
    assert calc["calc_inputs"]["p_density_dbw_hz"] == -74.0
    assert calc["margin_db"] == pytest.approx(7.0491, abs=1e-3)
    assert hits[0].details["data_quality_count"] == 1
    item = hits[0].details["data_quality"][0]
    assert (item["check"], item["seq_no"], item["pwr_ds_max"]) == ("density_above_total_power", 2, 73.0)
    assert "1 data-quality item" in hits[0].title
    assert pfd_summary(hits)["data_quality"] == {("ngso", "art21", "density_above_total_power"): 1}


def test_group_with_only_inconsistent_power_is_not_evaluated_at_face_value():
    reader = gso_reader(power=73.0, pep=-4.0)
    hits = run(reader)
    assert hits[0].details["calculations"] == []
    assert hits[0].details["data_quality_count"] == 1
    assert hits[0].details["missing"][0]["missing"] == ["pwr_ds_max"]
    assert hits[0].severity == "medium"


def test_statistical_outlier_is_evaluated_but_flagged_and_not_scored():
    # 1,200 rows at -70 and -60 give Q1 -70, Q3 -60 and a far-out fence at -30. Notice 1 sits at -20.
    other = [
        {"grp_id": i, "seq_no": 1, "pwr_ds_max": -70.0 if i % 2 else -60.0, "pep_max": 20.0} for i in range(10, 1210)
    ]
    reader = gso_reader(power=-20.0)
    reader.tables["notice"].append(notice(2, ntc_type="G"))
    reader.tables["grp"] += [{"grp_id": i, "ntc_id": 2, "beam_name": "9", "emi_rcp": "E"} for i in range(10, 1210)]
    reader.tables["emiss"] += other
    hits = [h for h in run(reader) if 1 in h.notice_ids]
    calc = only_calc(hits)
    flag = calc["data_quality_flags"][0]
    assert flag["check"] == "power_density_statistical_outlier"
    assert flag["fence_dbw_hz"] == -30.0
    assert calc["margin_db"] > 0
    assert hits[0].severity == "medium"
    assert hits[0].details["severity_score"] == 0
    assert "input flagged as a data-quality item" in hits[0].title


def test_flagged_input_does_not_outrank_an_ordinary_result_in_the_same_filing():
    # Same filler as above, fence -30. Group 1 at -20 has the larger margin but is flagged, group 2 at -40 is not.
    other = [
        {"grp_id": i, "seq_no": 1, "pwr_ds_max": -70.0 if i % 2 else -60.0, "pep_max": 20.0} for i in range(10, 1210)
    ]
    reader = gso_reader(power=-20.0)
    reader.tables["freq"].append(freq(1, 8100, 8110, grp=2))
    reader.tables["notice"].append(notice(2, ntc_type="G"))
    reader.tables["grp"] += [{"grp_id": 2, "ntc_id": 1, "beam_name": "1", "emi_rcp": "E"}]
    reader.tables["grp"] += [{"grp_id": i, "ntc_id": 2, "beam_name": "9", "emi_rcp": "E"} for i in range(10, 1210)]
    reader.tables["emiss"] += [{"grp_id": 2, "seq_no": 1, "pwr_ds_max": -40.0, "pep_max": 20.0}, *other]
    hit = next(h for h in run(reader) if 1 in h.notice_ids)
    assert hit.details["calc_inputs"]["group_id"] == 2
    assert hit.severity == "high" and hit.details["severity_score"] > 0
    assert "1 data-quality item" in hit.title


# --- Not visible and missing longitude


def test_gso_below_the_horizon_is_a_computed_result_not_a_concern():
    hits = run(gso_reader(lon=-131.0))
    assert len(hits) == 1
    hit = hits[0]
    assert hit.severity == "low" and hit.details["concern"] is False
    assert hit.details["missing"] == [] and hit.details["unevaluated_count"] == 0
    assert hit.details["not_visible_count"] == 1
    assert hit.details["not_visible"][0]["reason"] == "gso_below_horizon_from_every_finnish_point"
    assert "not visible from Finland" in hit.title and "missing" not in hit.title


def test_gso_with_a_missing_longitude_needs_review():
    hits = run(gso_reader(geo=False))
    assert len(hits) == 1
    hit = hits[0]
    assert hit.severity == "medium" and hit.details["concern"] is True
    assert hit.details["not_visible"] == []
    assert hit.details["unevaluated_by_reason"] == {"gso_longitude_missing": 1}
    assert "missing gso_longitude" in hit.title and "not visible" not in hit.title


def test_summary_counts_each_kind_of_outcome_apart():
    hits = run(gso_reader(lon=-131.0)) + run(gso_reader(geo=False))
    summary = pfd_summary(hits)
    assert summary["not_visible"] == {("gso", "art21", "gso_below_horizon_from_every_finnish_point"): 1}
    assert summary["unevaluated"] == {("gso", "art21", "gso_longitude_missing", "gso_longitude"): 1}
    assert summary["results"] == {}


def test_gso_margin_matches_hand_calculation_at_the_worst_point():
    # 25 E over the northernmost Finnish point, calculated here from the geometry and not by the engine.
    calc = only_calc(run(gso_reader()))
    point = calc["worst_point"]
    cos_psi = math.cos(math.radians(point["lat"])) * math.cos(math.radians(25.0 - point["lon"]))
    distance = math.sqrt(6378.0**2 + 42164.0**2 - 2 * 6378.0 * 42164.0 * cos_psi)
    angle = math.degrees(math.atan2(cos_psi - 6378.0 / 42164.0, math.sqrt(1 - cos_psi**2)))
    limit = -150 + 0.5 * (angle - 5) if 5 < angle < 25 else (-150 if angle <= 5 else -140)
    value = -60.0 + 10 * math.log10(4000) + 30 - 10 * math.log10(4 * math.pi * (distance * 1000) ** 2)
    assert calc["value_db"] == pytest.approx(value, abs=1e-9)
    assert calc["limit_db"] == pytest.approx(limit, abs=1e-9)
    assert point["lat"] == pytest.approx(max(p.lat_deg for p in finland_points()))


# --- Real filing


@pytest.fixture(scope="module")
def real_3074():
    if not SHARED_3074.exists():
        pytest.skip("shared filing unavailable")
    issue = SpaceIssue(MdbReader(SHARED_3074))
    return issue, list(PfdRule(get_settings()).run(issue))


def test_real_3074_row_matches_hand_pfd_calculation(real_3074):
    issue, hits = real_3074
    calculated = next(
        c for hit in hits for c in hit.details["calculations"]
        if c["method"] == "pfd_gso" and c["calc_inputs"]["group_id"] == 124711730
    )
    # These are the 3074 source rows, not rule output.
    longitude = issue.notices[124520179].gso_lon_deg
    freq_row = next(a for a in issue.notices[124520179].assignments if a["grp_id"] == 124711730)
    emiss_row = max(issue.emissions[124711730], key=lambda r: r["pwr_ds_max"])
    beam_row = next(r for r in issue.beams[(124520179, "E", "TB2")])
    assert longitude == -52.5
    assert (freq_row["freq_min"], freq_row["freq_max"]) == (17670.0, 17710.0)
    assert emiss_row["pwr_ds_max"] == pytest.approx(-48.599998474121094)
    assert beam_row["gain"] == 44.0
    point = calculated["worst_point"]
    re, rs = 6378.0, 42164.0
    cos_psi = math.cos(math.radians(point["lat"])) * math.cos(math.radians(longitude - point["lon"]))
    distance = math.sqrt(re**2 + rs**2 - 2 * re * rs * cos_psi)
    hand = emiss_row["pwr_ds_max"] + 10 * math.log10(1_000_000) + beam_row["gain"] - 10 * math.log10(4 * math.pi * (distance * 1000) ** 2)
    assert hand == pytest.approx(calculated["value_db"], abs=1e-9)


def test_real_3074_counts_that_must_not_drift(real_3074):
    _, hits = real_3074
    summary = pfd_summary(hits)
    assert summary["results"][("gso", "art21", "exceeds")] == 605
    assert summary["results"][("gso", "art21", "within_3db")] == 162
    assert summary["results"][("gso", "art21", "below")] == 684
    assert summary["unevaluated"][("ngso", "art21", "mask_parameter_not_evaluated", "X")] == 5398
    assert summary["unevaluated"][("ngso", "art21", "mask_parameter_not_evaluated", "Y")] == 31
    assert summary["not_visible"][("gso", "art21", "gso_below_horizon_from_every_finnish_point")] == 1622
    assert summary["data_quality"][("ngso", "art21", "density_above_total_power")] == 1
    assert not [h for h in hits if not h.details["calculations"] and "within" in h.title]
    # The 73 dBW/Hz row must not surface: nothing evaluated uses a density above zero.
    assert max(c["calc_inputs"]["p_density_dbw_hz"] for h in hits for c in h.details["calculations"]) < 0


def test_real_3074_flights_never_beat_the_upper_bound(real_3074):
    _, hits = real_3074
    calcs = [c for h in hits for c in h.details["calculations"] if c["method"] == "pfd_ngso"]
    flown = [c for c in calcs if c["propagation"].get("margin_db") is not None]
    assert flown
    assert all(c["propagation"]["margin_db"] <= c["envelope"]["margin_db"] + 0.02 for c in flown)
    assert all(c["result"] != "exceeds" or c["margin_basis"] == "propagated_sample" for c in calcs)


def test_real_3074_eccentric_filing_matches_hand_envelope(real_3074):
    # K-RADCUBE: perigee 200, apogee 70,606, inclination 39, no argument of perigee, so it is not flown.
    issue, hits = real_3074
    row = next(n for n in issue.notices.values() if n.name == "K-RADCUBE")
    orbit = issue.orbits[row.ntc_id][0]
    assert (orbit["perig_km"], orbit["apog_km"], orbit["inclin_ang"]) == (200.0, 70606.0, 39.0)
    assert orbit["perig_arg"] is None
    calc = next(c for h in hits for c in h.details["calculations"] if c["ntc_id"] == row.ntc_id)
    assert calc["propagation"]["kind"] == "not_run"
    psi = 59.81602 - 39.0  # central angle from the southernmost Finnish point
    needed = 6378.0 / math.cos(math.radians(psi)) - 6378.0
    assert needed > 200  # it can't see Finland from perigee
    assert calc["envelope"]["central_angle_min_deg"] == pytest.approx(psi)
    assert calc["envelope"]["altitude_km"] >= needed - 1e-6


def test_pep_max_caps_power_in_the_reference_bandwidth():
    uncapped = only_calc(run(gso_reader(power=-60.0, pep=20.0)))
    capped = only_calc(run(gso_reader(power=-60.0, pep=-40.0)))
    ref = 10 * math.log10(capped["calc_inputs"]["ref_bw_hz"])
    assert uncapped["calc_inputs"]["power_in_ref_bw_dbw"] == pytest.approx(-60.0 + ref)
    assert capped["calc_inputs"]["power_in_ref_bw_dbw"] == -40.0
    assert capped["value_db"] == pytest.approx(uncapped["value_db"] - (-60.0 + ref + 40.0))


def test_pep_max_caps_non_gso_power_too():
    orbit = {"perig_km": 600.0, "apog_km": 600.0, "inclin_ang": 97.0}
    uncapped = only_calc(run(ngso_reader([orbit])))
    reader = ngso_reader([orbit])
    reader.tables["emiss"][0]["pep_max"] = -40.0
    capped = only_calc(run(reader))
    drop = uncapped["calc_inputs"]["power_in_ref_bw_dbw"] + 40.0
    assert capped["calc_inputs"]["peak_power_dbw"] == -40.0
    assert capped["envelope"]["margin_db"] == pytest.approx(uncapped["envelope"]["margin_db"] - drop)


def test_worst_row_is_chosen_after_the_cap_not_by_density():
    reader = gso_reader()
    reader.tables["emiss"] = [
        {"grp_id": 1, "seq_no": 1, "pwr_ds_max": -60.0, "pep_max": -40.0},  # densest, but capped hard
        {"grp_id": 1, "seq_no": 2, "pwr_ds_max": -65.0, "pep_max": 20.0},
    ]
    calc = only_calc(run(reader))
    ref = 10 * math.log10(calc["calc_inputs"]["ref_bw_hz"])
    assert calc["calc_inputs"]["emission_seq_no"] == 2
    assert calc["calc_inputs"]["power_in_ref_bw_dbw"] == pytest.approx(-65.0 + ref)
