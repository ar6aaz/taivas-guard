"""Terrestrial findings: field strength, honest gaps, the previous issue, the funnel, the full pipeline, export.

Expected values come from typed-in T/R 25-08 figures, plain arithmetic, or calls to the engine with
typed-in inputs, never from the rule's own output.
"""
import hashlib
import json
import math
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from ific_guard.config import get_settings
from ific_guard.db import session_scope
from ific_guard.engines.p1546 import PathSegment, field_strength
from ific_guard.export import export_issue
from ific_guard.models import Artifact, Case, CaseOccurrence, Event, Notification
from ific_guard.notifier.runner import handle_issue_screened
from ific_guard.screening import stations as stations_module
from ific_guard.screening.engine import handle_issue_fetched, screen_artifact
from ific_guard.screening.space import MdbReader
from ific_guard.terrestrial import rule_field_strength as rule_module
from ific_guard.terrestrial.adapter import TerrestrialIssue
from ific_guard.terrestrial.rule_field_strength import (
    NOT_CALCULATED_RULE_ID,
    RULE_ID,
    FieldStrengthRule,
    OutsideLandMask,
    PreviousIssue,
    find_previous_issue,
    is_land,
    path_segments,
)
from tests.test_terrestrial import reader as base_reader

# A transmitter in Karelia and one evaluation point due west of it, so geometry can be checked by hand.
TX_LAT, TX_LON = 61.76, 30.99
POINT_LAT, POINT_LON = 61.76, 30.0
DISTANCE_KM = 6371.0088 * math.acos(
    math.sin(math.radians(TX_LAT)) ** 2 + math.cos(math.radians(TX_LAT)) ** 2 * math.cos(math.radians(TX_LON - POINT_LON))
)


def antenna(key=1, ant_key=1, **over):
    return {"terrakey": key, "ant_key": ant_key, "tx_rx": "TX", "pwr_dbw": 17.4, "pwr_eiv": "E", "pwr_xyz": "Y",
            "gain_max": 8.2, "hgt_agl": 26.0, "eff_hgtmax": None, "azm_max_e": 176.0, "ant_dir": "D", **over}


def tables(*assignments, issue=3076, antennas=None, freq=152.9, bandwidth=16.8, targets=()):
    r = base_reader(issue, list(assignments), targets=targets)
    r.tables["fxm_ant"] = antennas if antennas is not None else [antenna(a[0]) for a in assignments]
    for row in r.tables["fxm_terra"]:
        row.update(freq_assgn=freq, freq_min=freq - bandwidth / 2000, freq_max=freq + bandwidth / 2000, bdwdth=bandwidth,
                   prov="RR11.2", fragment="NTFD_RR")
    for row in r.tables["fxm_geo"]:
        row.update(lat_dec=TX_LAT, long_dec=TX_LON, site_alt=120.0, ctry="RUS", site_name="TEST SITE")
    return r


def previous(status="no_artifact", issue_no=3075, issue=None):
    return PreviousIssue(issue_no, issue, status, None, f"previous: {status}")


@pytest.fixture
def one_point(monkeypatch):
    monkeypatch.setattr(rule_module, "_boundary_points", lambda: ((POINT_LAT, POINT_LON),))


def run(r, prev=None):
    rule = FieldStrengthRule(get_settings(), prev or previous(), "s" * 64, stations=[])
    hits = rule.run(TerrestrialIssue(r))
    return hits, rule.funnel


def direct(**over):
    arguments = dict(frequency_mhz=152.9, time_percent=10, tx_effective_height_m=26.0, tx_height_agl_m=26.0,
                     rx_height_m=10, rx_clutter_height_m=10, rx_area="rural",
                     segments=[PathSegment(DISTANCE_KM, "land")], erp_kw=10 ** ((17.4 - 30) / 10))
    return field_strength(**{**arguments, **over}).field_strength_dbuv_m


def test_field_strength_threshold_and_margin_match_a_direct_engine_call(one_point):
    hits, _ = run(tables((1, "RUS", "FB", "ADD")))
    hit = hits[0]
    d = hit.details
    expected = direct()
    assert DISTANCE_KM == pytest.approx(52.2, abs=0.2)  # 0.99 deg of longitude at 61.76 N
    assert d["value_db"] == pytest.approx(expected, abs=0.005)
    assert d["limit_db"] == 12  # T/R 25-08 A1.2.3, 108 to 380 MHz, p.6
    assert d["margin_db"] == pytest.approx(expected - 12, abs=0.01)
    assert d["worst_point"]["distance_km"] == pytest.approx(DISTANCE_KM, abs=0.001)
    assert d["worst_point"]["bearing_from_tx_deg"] == pytest.approx(270, abs=1)
    assert d["calc_inputs"]["erp_dbw"] == 17.4
    assert d["method"] == "p1546" and d["mask_id"] == "tr2508_a1_108_380"
    assert hit.family_id == 1 and hit.notice_ids == [1] and hit.comment_deadline is None


@pytest.mark.parametrize(("frequency", "threshold"), [
    (40.0, 0), (60.0, 6), (152.9, 12), (390.0, 18), (425.0, 20),  # typed from T/R 25-08 A1.2.3, p.6
])
def test_threshold_comes_from_the_annex_1_band(one_point, frequency, threshold):
    hit = run(tables((1, "RUS", "FB", "ADD"), freq=frequency))[0][0]
    assert hit.details["limit_db"] == threshold


def test_shared_band_edge_uses_the_stricter_value(one_point):
    assert run(tables((1, "RUS", "FB", "ADD"), freq=380.0))[0][0].details["limit_db"] == 12


def test_bandwidth_above_25_khz_adds_the_annex_1_conversion(one_point):
    d = run(tables((1, "RUS", "FB", "ADD"), bandwidth=50.0))[0][0].details
    assert d["limit_db"] == pytest.approx(12 + 3.01, abs=0.005)  # 10 log10(50/25)
    assert any("stands in for channel spacing" in a for a in d["assumptions"])


def test_above_470_mhz_uses_the_400_470_threshold_as_traficom_said(one_point):
    hit = run(tables((1, "RUS", "FB", "ADD"), freq=793.5, bandwidth=25.0))[0][0]
    d = hit.details
    assert d["assessment"] in {"above_indicative_threshold", "below_indicative_threshold"}
    assert d["limit_db"] == 20 and d["mask_id"] == "tr2508_a1_400_470_traficom_above_470"
    assert d["value_db"] == pytest.approx(direct(frequency_mhz=793.5), abs=0.005)
    assert d["criterion"]["extension"]["source_band_mhz"] == [400, 470]
    assert "Traficom said to apply above 470 MHz" in hit.title and "400-470 MHz" in hit.title
    assert "extended above 470 MHz" in d["framework"]["cept_ecc"]["applies"]
    assert any("Annexes 3 and 4 apply" in n for n in d["needs_info"])
    docs = [c["doc"] for c in d["citations"]]
    assert any(doc.startswith("Traficom answer") for doc in docs) and any("29.7-470" in c["clause"] for c in d["citations"])


@pytest.mark.parametrize(("frequency", "mask_id"), [
    (469, "tr2508_a1_400_470"),
    (470, "tr2508_a1_400_470"),
    (471, "tr2508_a1_400_470_traficom_above_470"),
    (4000, "tr2508_a1_400_470_traficom_above_470"),
    (4001, None),
])
def test_criterion_edges_around_470_and_4000_mhz(frequency, mask_id):
    mask = rule_module.annex1_mask(frequency)
    assert (mask["mask_id"] if mask else None) == mask_id
    assert mask is None or mask["breakpoints"] == [[0, 20]]


def test_without_a_criterion_the_value_is_reported_without_calling_it_an_exceedance(one_point, monkeypatch):
    masks = {**rule_module._masks(), "extensions": []}
    monkeypatch.setattr(rule_module, "_masks", lambda: masks)
    hit = run(tables((1, "RUS", "FB", "ADD"), freq=793.5, bandwidth=5000.0))[0][0]
    d = hit.details
    assert d["assessment"] == "calculated_no_criterion" and d["criterion"]["status"] == "none_available"
    assert not {"limit_db", "margin_db", "mask_id"} & d.keys()
    assert "no comparison" in hit.title and "above" not in hit.title and "exceed" not in hit.title.lower()
    assert any("Which criterion applies" in n for n in d["needs_info"])
    assert d["citations"][-1]["doc"] == "M4" and any("29.7-470" in c["clause"] for c in d["citations"])


def test_applicability_on_the_russian_border_is_stated_as_unknown(one_point):
    d = run(tables((1, "RUS", "FB", "ADD")))[0][0].details
    assert "does not say whether its values apply" in d["criterion"]["border_applicability"]
    assert any("Russia border" in n for n in d["needs_info"])
    assert "not a limit" in d["criterion"]["meaning"]


def test_above_threshold_title_says_possible_concern_and_not_violation(one_point):
    hit = run(tables((1, "RUS", "FB", "ADD")))[0][0]
    assert hit.title.startswith("Possible concern:") and "under stated assumptions" in hit.title
    assert "violation" not in hit.title.lower() and "breach" not in hit.title.lower()


def test_eirp_is_converted_to_erp(one_point):
    erp = run(tables((1, "RUS", "FB", "ADD")))[0][0].details["value_db"]
    eirp = run(tables((1, "RUS", "FB", "ADD"), antennas=[antenna(pwr_eiv="I")]))[0][0].details["value_db"]
    assert erp - eirp == pytest.approx(2.15, abs=0.01)


def test_the_azimuth_height_towards_the_point_is_used(one_point):
    r = tables((1, "RUS", "FB", "ADD"))
    r.tables["fxm_ant_hgt"] = [{"terrakey": 1, "ant_key": 1, "azm": float(a), "eff_hgt": 90 if a == 270 else 5} for a in range(0, 360, 10)]
    d = run(r)[0][0].details
    assert d["value_db"] == pytest.approx(direct(tx_effective_height_m=90.0), abs=0.005)
    assert d["calc_inputs"]["effective_height_source"] == "fxm_ant_hgt, azimuth 270 deg"


def test_sectors_at_one_height_share_the_only_azimuth_table(one_point):
    r = tables((1, "RUS", "FB", "ADD"), antennas=[antenna(ant_key=1, eff_hgtmax=500.0), antenna(ant_key=2, eff_hgtmax=500.0)])
    r.tables["fxm_ant_hgt"] = [{"terrakey": 1, "ant_key": 1, "azm": 270.0, "eff_hgt": 40}]
    d = run(r)[0][0].details
    assert d["value_db"] == pytest.approx(direct(tx_effective_height_m=40.0), abs=0.005)


def test_the_strongest_antenna_and_the_stronger_point_are_reported(monkeypatch):
    near_lon = 30.5
    monkeypatch.setattr(rule_module, "_boundary_points", lambda: ((POINT_LAT, POINT_LON), (POINT_LAT, near_lon)))
    r = tables((1, "RUS", "FB", "ADD"), antennas=[antenna(ant_key=1, pwr_dbw=5.0), antenna(ant_key=2, pwr_dbw=15.0)])
    d = run(r)[0][0].details
    assert d["worst_point"]["lon"] == near_lon and d["calc_inputs"]["antenna"]["ant_key"] == 2
    assert d["calc_inputs"]["search"]["calculations"] == 4


@pytest.mark.parametrize(("rows", "reason"), [
    ([antenna(hgt_agl=None)], "missing_antenna_height"),
    ([antenna(pwr_dbw=None)], "missing_power"),
    ([antenna(pwr_eiv=None)], "unknown_power_reference"),
    ([antenna(tx_rx="RX")], "no_transmit_antenna_row"),
    ([], "no_transmit_antenna_row"),
    ([antenna(hgt_agl=0.5)], "outside_engine_range"),
])
def test_missing_inputs_become_a_flagged_finding_and_never_a_clear_result(one_point, rows, reason):
    hits, funnel = run(tables((1, "RUS", "FB", "ADD"), antennas=rows))
    hit = hits[0]
    d = hit.details
    assert hit.rule_id == NOT_CALCULATED_RULE_ID and hit.severity == "medium"
    assert d["assessment"] == "not_calculated" and d["not_calculated_reason"] == reason
    assert d["method"] == "screening" and not {"value_db", "limit_db", "margin_db"} & d.keys()
    assert hit.title.startswith("Not calculated, expert review needed") and "not a clear result" in d["statement"]
    assert funnel["stages"][-1]["not_calculated_by_reason"] == {reason: 1} and funnel["stages"][-1]["kept"] == 0


def test_frequency_outside_the_engine_range_is_flagged(one_point):
    hit = run(tables((1, "RUS", "FB", "ADD"), freq=20.0))[0][0]
    assert hit.details["not_calculated_reason"] == "outside_engine_range"


def test_missing_coordinates_are_flagged_though_the_filter_drops_them(one_point):
    r = tables((1, "RUS", "FB", "ADD"))
    r.tables["fxm_geo"][0].update(lat_dec=None)
    hits, funnel = run(r)
    assert [h.details["not_calculated_reason"] for h in hits] == ["missing_coordinates"]
    assert funnel["stages"][3]["no_usable_position_flagged"] == 1 and funnel["stages"][3]["kept"] == 0


def test_a_withdrawal_has_nothing_to_calculate_and_says_so(one_point):
    hit = run(tables((1, "RUS", "FB", "SUPPRESS"), targets=[(1, 9, "SUPPRESS")]))[0][0]
    assert hit.details["not_calculated_reason"] == "withdrawal" and hit.severity == "low"


def test_a_path_outside_the_land_mask_is_flagged(monkeypatch, one_point):
    def outside(*args):
        raise OutsideLandMask("outside")
    monkeypatch.setattr(rule_module, "path_segments", outside)
    assert run(tables((1, "RUS", "FB", "ADD")))[0][0].details["not_calculated_reason"] == "path_outside_land_mask"


def test_implausible_inputs_are_data_quality_items_and_not_top_findings(one_point):
    good = run(tables((1, "RUS", "FB", "ADD")))[0][0]
    assert good.severity == "high" and good.details["data_quality"] == []
    for rows, text in (
        ([antenna(pwr_dbw=75.0)], "implausibly high"),
        ([antenna(hgt_agl=900.0)], "implausibly high"),
        ([antenna(eff_hgtmax=2000.0)], "implausible"),
    ):
        bad = run(tables((1, "RUS", "FB", "ADD"), antennas=rows))[0][0]
        assert any(text in item for item in bad.details["data_quality"])
        assert bad.severity == "medium" and "capped at medium" in bad.details["severity_reasons"][-1]


def test_low_implied_antenna_input_is_flagged():
    # -2.2 dBW e.r.p. with 14 dBi gain: -2.2 - 14 + 2.15 = -14.05 dBW into the antenna
    notice = TerrestrialIssue(tables((1, "RUS", "FB", "ADD"), antennas=[antenna(pwr_dbw=-2.2, gain_max=14.0, hgt_agl=36.0)])).notices["1"]
    items = rule_module.data_quality_items(notice, rule_module.transmit_antennas(notice))
    assert "implies -14" in items[0] and "dBW into the antenna" in items[0]


def test_below_threshold_is_low_unless_an_alternative_crosses_it(one_point, monkeypatch):
    far_lon = 24.0  # about 330 km: well below 12 dB(uV/m) on land at 10% time, with weak power
    monkeypatch.setattr(rule_module, "_boundary_points", lambda: ((POINT_LAT, far_lon),))
    hit = run(tables((1, "RUS", "FB", "ADD"), antennas=[antenna(pwr_dbw=-20.0)]))[0][0]
    assert hit.details["assessment"] == "below_indicative_threshold" and hit.details["margin_db"] < 0
    assert hit.severity == "low" and "Possible concern" not in hit.title
    r = tables((1, "RUS", "FB", "ADD"), antennas=[antenna(pwr_dbw=-20.0)])
    r.tables["fxm_coord"] = [{"terrakey": 1, "adm": "FIN", "coord_prov": "RR6.7", "coord_st": "X"}]
    assert run(r)[0][0].severity == "medium"  # Finland is named in the notice's coordination rows


def test_declared_completed_coordination_with_finland_lowers_rank_and_is_shown(one_point):
    r = tables((1, "RUS", "FB", "ADD"))
    r.tables["fxm_coord"] = [{"terrakey": 1, "adm": "FIN", "coord_prov": "RR6.7", "coord_st": "COORD COMPLETED", "decl_affct": "NOTIFIER"}]
    hit = run(r)[0][0]
    assert hit.severity == "medium" and hit.details["fxm_coord"]["fin_completed_declared_by"] == ["NOTIFIER"]
    assert "NOTIFIER declares FIN coordination completed, please verify" in hit.title
    r.tables["fxm_coord"][0].update(adm="EST")  # another country's coordination says nothing about Finland
    assert run(r)[0][0].severity == "high"


def test_sensitivity_flags_when_time_percentage_or_path_type_crosses_the_threshold(one_point):
    value, limit = 10.0, 12
    crossed = FieldStrengthRule._crossed_by(value, limit, {"all_land_dbuv_m": 11.0, "all_sea_dbuv_m": 30.0, "time_1_percent_dbuv_m": 13.0})
    assert crossed == ["all-sea path", "1% time"]
    assert FieldStrengthRule._crossed_by(20.0, limit, {"all_land_dbuv_m": 20.0, "all_sea_dbuv_m": 30.0, "time_1_percent_dbuv_m": 25.0}) == []


def test_special_interest_station_gets_its_own_calculated_field(one_point):
    station = stations_module.Station(
        row=5, station_no=77, licence_class=None, lon_deg=POINT_LON, lat_deg=POINT_LAT, ground_height_m=None, mast_height_m=None,
        ant_height_agl_m=20.0, ant_height_asl_m=None, ant_gain_db=None, tx_mhz=None, rx_mhz=152.9, channel_bw_mhz=0.025,
        transmission_bw_mhz=None, transmission_class=None, radiation_direction_deg=None, polarisation=None, power=None,
        power_unit=None, power_itu=None, line_loss_db=None, directivity=None,
    )
    rule = FieldStrengthRule(get_settings(), previous(), "s" * 64, stations=[station])
    d = rule.run(TerrestrialIssue(tables((1, "RUS", "FB", "ADD"))))[0].details
    assert d["station_ids"] == ["77@R5"] and d["special_interest"] is True
    assert d["stations_overlapping"][0]["value_db"] == pytest.approx(direct(rx_height_m=20.0), abs=0.005)
    assert d["stations_overlapping"][0]["rx_height_m"] == 20.0
    assert d["stations_overlapping"][0]["matched_bands"][0]["side"] == "rx"
    assert "field_location" not in d["stations_overlapping"][0]


def test_tx_match_says_its_field_is_not_at_the_receiver(one_point):
    station = stations_module.Station(
        row=5, station_no=77, licence_class=None, lon_deg=POINT_LON, lat_deg=POINT_LAT, ground_height_m=None, mast_height_m=None,
        ant_height_agl_m=20.0, ant_height_asl_m=None, ant_gain_db=None, tx_mhz=152.9, rx_mhz=None, channel_bw_mhz=0.025,
        transmission_bw_mhz=None, transmission_class=None, radiation_direction_deg=None, polarisation=None, power=None,
        power_unit=None, power_itu=None, line_loss_db=None, directivity=None,
    )
    rule = FieldStrengthRule(get_settings(), previous(), "s" * 64, stations=[station])
    d = rule.run(TerrestrialIssue(tables((1, "RUS", "FB", "ADD"))))[0].details
    entry = d["stations_overlapping"][0]
    assert d["special_interest"] is True and entry["matched_bands"][0]["side"] == "tx"
    assert entry["field_location"].startswith("station site, not the receiver")


def test_land_mask_knows_the_gulf_of_finland():
    assert is_land(61.5, 25.0) and is_land(59.3, 25.0) and is_land(64.0, 32.0)
    assert not is_land(59.9, 26.5) and not is_land(59.0, 20.0)
    with pytest.raises(OutsideLandMask):
        is_land(45.0, 25.0)


def test_a_path_across_the_gulf_is_mixed_and_its_lengths_add_up():
    tallinn, helsinki = (59.2, 24.8), (60.4, 25.0)
    segments = path_segments(*tallinn, *helsinki)
    assert [s.kind for s in segments] == ["land", "cold_sea", "land"]
    from ific_guard.terrestrial.adapter import _haversine_km
    assert sum(s.length_km for s in segments) == pytest.approx(_haversine_km(*tallinn, *helsinki))


def test_funnel_counts_each_stage_and_says_silent_neighbours_were_not_checked(one_point):
    r = tables((1, "RUS", "FB", "ADD"), (2, "RUS", "ML", "ADD"), (3, "CHN", "FB", "ADD"), (4, "RUS", "FB", "ADD"), (5, "RUS", "FX", "ADD"))
    r.tables["fxm_geo"][3].update(lat_dec=56.0, long_dec=60.0)  # far beyond 300 km
    r.tables["fxm_ant"] = [antenna(1), antenna(2, hgt_agl=None)]
    r.tables["fxm_coord"] = [{"terrakey": 5, "adm": "FIN", "coord_prov": "RR6.7"}]
    hits, funnel = run(r)
    stages = {s["stage"]: s for s in funnel["stages"]}
    assert stages["published_in_issue"]["kept"] == 5
    assert (stages["land_mobile"]["kept"], stages["land_mobile"]["removed"]) == (4, 1)
    assert stages["land_mobile"]["removed_by_station_class"] == {"FX": 1}
    assert stages["land_mobile"]["neighbour_notices_removed_within_distance_limit"] == 1
    assert (stages["administration"]["kept"], stages["administration"]["removed_by_adm"]) == (3, {"CHN": 1})
    assert (stages["distance"]["kept"], stages["distance"]["removed"]) == (2, 1)
    assert stages["calculated"]["kept"] == 1 and stages["calculated"]["not_calculated_notices"] == ["2"]
    assert funnel["neighbours"]["RUS"]["changes"] == {"new": 3}
    assert funnel["neighbours"]["S"]["land_mobile_notices"] == 0 and "not a clean result" in funnel["neighbours"]["S"]["statement"]
    assert funnel["coordination_naming_finland"]["not_land_mobile"] == ["5"]
    assert hits[0].details["filter_counts"]["within_distance"] == 2


def _stored(tmp_path, issue=3076, sha=None, write=True):
    path = tmp_path / f"brific{issue}.mdb"
    if write:
        path.write_bytes(b"test database")
    with session_scope() as s:
        row = Artifact(domain="terrestrial", issue_no=issue, source_url=f"https://www.itu.int/brific{issue}.zip",
                       sha256=sha or hashlib.sha256(str(issue).encode()).hexdigest(), size_bytes=13, mdb_path=str(path))
        s.add(row)
        s.flush()
        return row.id


def _readers(monkeypatch, by_issue):
    def open_reader(path):
        for issue, reader in by_issue.items():
            if str(issue) in str(path):
                return reader
        raise AssertionError(path)
    monkeypatch.setattr(rule_module, "MdbReader", open_reader)
    monkeypatch.setattr("ific_guard.screening.engine.MdbReader", open_reader)
    monkeypatch.setattr("ific_guard.export.MdbReader", open_reader)


def test_previous_issue_is_found_by_number_and_used(tmp_path, monkeypatch, one_point):
    _stored(tmp_path, 3075)
    _readers(monkeypatch, {3075: tables((9, "RUS", "FB", "ADD"), issue=3075)})
    found = find_previous_issue(3076)
    assert (found.status, found.issue_no, found.issue.issue_no) == ("available", 3075, 3075)
    r = tables((1, "RUS", "FB", "MODIFY"), targets=[(1, 9, "MODIFY")])
    hit = run(r, found)[0][0]
    assert hit.details["change"]["kind"] == "modified" and hit.details["change"]["target_in_previous"] is True
    assert hit.details["change"]["present_in_previous"] is False
    assert hit.family_id == 9  # shares a case with the notice it modifies


def test_missing_previous_issue_still_screens_and_says_what_was_not_checked(tmp_path, monkeypatch, one_point):
    artifact_id = _stored(tmp_path)
    _readers(monkeypatch, {3076: tables((1, "RUS", "FB", "ADD"))})
    monkeypatch.setattr(rule_module, "load_stations", lambda: [])
    assert screen_artifact(artifact_id)["status"] == "done"
    with session_scope() as s:
        d = s.scalars(select(Case)).one().details
    assert d["change"]["previous_issue_status"] == "no_artifact"
    assert d["change"]["present_in_previous"] is None and d["change"]["target_in_previous"] is None
    assert d["change"]["kind"] == "new"
    assert any("Issue 3075 is not stored" in a for a in d["assumptions"])


def test_previous_issue_with_a_lost_file_is_reported_as_such(tmp_path):
    _stored(tmp_path, 3075, write=False)
    found = find_previous_issue(3076)
    assert found.status == "file_missing" and found.issue is None and "file is missing" in found.note


def test_an_unusable_previous_issue_fails_screening_visibly(tmp_path, monkeypatch, one_point):
    _stored(tmp_path, 3075)
    artifact_id = _stored(tmp_path)
    _readers(monkeypatch, {3075: tables((9, "RUS", "FB", "ADD"), issue=3074), 3076: tables((1, "RUS", "FB", "ADD"))})
    with pytest.raises(ValueError, match="Expected terrestrial issue 3075"):
        handle_issue_fetched({"artifact_id": artifact_id})
    with session_scope() as s:
        assert s.get_one(Artifact, artifact_id).screen_status == "failed"
        assert s.scalars(select(Case)).first() is None


def test_full_pipeline_takes_a_fake_terrestrial_artifact_through_to_a_digest(tmp_path, monkeypatch, one_point):
    """Same events, cases and notifier as space: fetched, screened, then one digest."""
    artifact_id = _stored(tmp_path)
    r = tables((1, "RUS", "FB", "ADD"), (2, "RUS", "ML", "ADD"))
    r.tables["fxm_ant"] = [antenna(1), antenna(2, hgt_agl=None)]
    _readers(monkeypatch, {3076: r})
    monkeypatch.setattr(rule_module, "load_stations", lambda: [])

    handle_issue_fetched({"artifact_id": artifact_id})
    with session_scope() as s:
        artifact = s.get_one(Artifact, artifact_id)
        assert artifact.screen_status == "done"
        event = s.scalars(select(Event)).one()
        assert (event.topic, event.payload["status"], event.payload["opened"]) == ("issue.screened", "done", 2)
        cases = {c.rule_id: c for c in s.scalars(select(Case))}
        assert set(cases) == {RULE_ID, NOT_CALCULATED_RULE_ID}
        assert cases[RULE_ID].domain == "terrestrial" and cases[RULE_ID].notice_ids == ["1"]
        assert cases[RULE_ID].details["value_db"] == pytest.approx(direct(), abs=0.005)
        assert s.scalars(select(CaseOccurrence)).all().__len__() == 2

    handle_issue_screened(event.payload)
    with session_scope() as s:
        item = s.scalars(select(Notification)).one()
    assert item.subject == "BR IFIC 3076 (terrestrial): 2 possible concerns for review, 1 high"
    assert "Possible concern: TEST SITE (RUS) 152.9 MHz" in item.body
    assert "Not calculated, expert review needed: TEST SITE" in item.body
    assert "None is a confirmed breach" in item.body and "brific3076" in item.body

    assert screen_artifact(artifact_id)["opened"] == 0  # idempotent re-screen
    assert screen_artifact(artifact_id)["changed"] == 0


def test_an_empty_issue_screens_done_with_no_cases(tmp_path, monkeypatch):
    artifact_id = _stored(tmp_path)
    _readers(monkeypatch, {3076: tables((1, "CHN", "FB", "ADD"))})
    monkeypatch.setattr(rule_module, "load_stations", lambda: [])
    assert screen_artifact(artifact_id) == {"artifact_id": artifact_id, "status": "done", "opened": 0, "changed": 0}


def test_export_contains_every_required_field_and_repeats_identically(tmp_path, monkeypatch, one_point):
    _stored(tmp_path)
    r = tables((1, "RUS", "FB", "ADD"), (2, "RUS", "ML", "ADD"))
    r.tables["fxm_ant"] = [antenna(1), antenna(2, hgt_agl=None)]
    _readers(monkeypatch, {3076: r})
    monkeypatch.setattr(rule_module, "load_stations", lambda: [])
    first, second = tmp_path / "one", tmp_path / "two"
    manifest = export_issue("terrestrial", 3076, first)
    export_issue("terrestrial", 3076, second)
    for name in ("cases.json", "cases.csv", "funnel.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
        assert manifest["output_sha256"][name] == hashlib.sha256((first / name).read_bytes()).hexdigest()
    records = json.loads((first / "cases.json").read_text(encoding="utf-8"))
    assert [r["notice_ids"] for r in records] == [["1"], ["2"]]
    calculated, skipped = records
    d = calculated["details"]
    assert d["source"]["rows"]["fxm_terra"]["terrakey"] == 1 and d["source"]["issue_no"] == 3076
    assert d["source"]["sha256"] == hashlib.sha256(b"3076").hexdigest()
    assert d["value_db"] == pytest.approx(direct(), abs=0.005)
    assert d["calc_inputs"]["frequency_mhz"] == 152.9 and d["calc_inputs"]["time_percent"] == 10
    assert d["calc_inputs"]["implementation"]["model"] == "ITU-R P.1546-6 (08/2019)"
    assert any("Terrain is out of scope" in a for a in d["assumptions"])
    assert {c["doc"] for c in d["citations"]} >= {"ITU-R P.1546-6 (08/2019)", "ECC Recommendation T/R 25-08 (edition 6 June 2025)"}
    assert skipped["details"]["not_calculated_reason"] == "missing_antenna_height"
    assert manifest["rule_ids"] == [RULE_ID, NOT_CALCULATED_RULE_ID] and manifest["config"]["neighbour_adms"]
    assert set(manifest["config"]["data_sha256"]) == {"field_strength.json", "finland_boundary.json", "land_near_finland.json"}
    header = (first / "cases.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "value_db,limit_db,margin_db,mask_id" in header and "not_calculated_reason" in header


# Real data. Set IFIC_SHARED_DATA to the folder holding brific3075.mdb and brific3076.mdb.
SHARED = Path(os.environ.get("IFIC_SHARED_DATA", Path(__file__).parents[1] / "data" / "shared"))
needs_real_data = pytest.mark.skipif(not (SHARED / "brific3076.mdb").exists(), reason="shared terrestrial databases not present")


@pytest.fixture(scope="module")
def real_3076():
    current = TerrestrialIssue(MdbReader(str(SHARED / "brific3076.mdb")), expected_issue=3076)
    earlier = TerrestrialIssue(MdbReader(str(SHARED / "brific3075.mdb")), expected_issue=3075)
    settings = get_settings()
    rule = FieldStrengthRule(settings, PreviousIssue(3075, earlier, "available", None, "Issue 3075 was read."), "s" * 64)
    hits = {str(h.notice_ids[0]): h for h in rule.run(current)}
    return current, hits, rule.funnel


@needs_real_data
def test_real_3076_funnel(real_3076):
    _, hits, funnel = real_3076
    stages = {s["stage"]: s for s in funnel["stages"]}
    assert stages["published_in_issue"]["kept"] == 3481
    assert stages["land_mobile"]["kept"] == 1858 and stages["land_mobile"]["removed"] == 1623
    assert stages["administration"]["kept"] == 114 and stages["distance"]["kept"] == 29 and stages["distance"]["removed"] == 85
    assert funnel["neighbours"]["RUS"]["changes"] == {"modified": 11, "new": 103}
    assert all(funnel["neighbours"][adm]["land_mobile_notices"] == 0 for adm in ("S", "EST", "NOR", "DNK"))
    assert stages["calculated"]["kept"] == 21 and stages["calculated"]["not_calculated_by_reason"] == {"missing_antenna_height": 8}
    assert len(hits) == 29


def _source_values(issue, terrakey):
    notice = issue.notices[terrakey]
    rows = notice.raw["fxm_ant"]
    return notice, rows


@needs_real_data
def test_real_kostomuksha_hand_check(real_3076):
    issue, hits, _ = real_3076
    notice, rows = _source_values(issue, "93082383")
    # Typed from the source rows: fxm_terra, fxm_geo, fxm_ant.
    assert (notice.frequency_mhz, notice.latitude_deg) == (152.025, pytest.approx(64.6336111, abs=1e-6))
    assert [(r["pwr_dbw"], r["pwr_eiv"], r["hgt_agl"], r["eff_hgtmax"]) for r in rows] == [(15.9, "E", 28.0, None)]
    point = hits["93082383"].details["worst_point"]
    expected = field_strength(frequency_mhz=152.025, time_percent=10, tx_effective_height_m=28.0, tx_height_agl_m=28.0,
                              rx_height_m=10, rx_clutter_height_m=10, rx_area="rural",
                              segments=[PathSegment(point["distance_km"], "land")], erp_kw=10 ** ((15.9 - 30) / 10))
    assert hits["93082383"].details["value_db"] == pytest.approx(expected.field_strength_dbuv_m, abs=0.01)
    assert hits["93082383"].details["limit_db"] == 12


@needs_real_data
def test_real_ruskeala_hand_check(real_3076):
    issue, hits, _ = real_3076
    notice, rows = _source_values(issue, "93082392")
    d = hits["93082392"].details
    assert notice.frequency_mhz == 793.5 and {r["pwr_dbw"] for r in rows} == {4.8} and {r["hgt_agl"] for r in rows} == {55.0}
    used = d["calc_inputs"]["antenna"]["ant_key"]
    azimuth = float(d["calc_inputs"]["effective_height_source"].split("azimuth ")[1].split(" ")[0])
    table = [r["eff_hgt"] for r in notice.raw["fxm_ant_hgt"] if r["azm"] == azimuth]
    assert used in {r["ant_key"] for r in rows} and len(table) == 1
    assert abs(azimuth - d["worst_point"]["bearing_from_tx_deg"]) <= 5
    expected = field_strength(frequency_mhz=793.5, time_percent=10, tx_effective_height_m=float(table[0]), tx_height_agl_m=55.0,
                              rx_height_m=10, rx_clutter_height_m=10, rx_area="rural",
                              segments=[PathSegment(d["worst_point"]["distance_km"], "land")], erp_kw=10 ** ((4.8 - 30) / 10))
    assert d["value_db"] == pytest.approx(expected.field_strength_dbuv_m, abs=0.01)
    # Above 470 MHz: the 400-470 MHz Annex 1 threshold of 20 dB(uV/m) per 25 kHz, as Traficom said (ticket 20)
    bandwidth = notice.bandwidth_khz
    limit = 20 + (10 * math.log10(bandwidth / 25) if bandwidth and bandwidth > 25 else 0)
    assert d["limit_db"] == pytest.approx(limit, abs=0.005)
    assert d["margin_db"] == pytest.approx(d["value_db"] - d["limit_db"], abs=0.01)


@needs_real_data
def test_real_kingisepp_hand_check_on_a_mixed_path(real_3076):
    issue, hits, _ = real_3076
    notice, rows = _source_values(issue, "93075624")
    d = hits["93075624"].details
    assert notice.frequency_mhz == 425.825 and [(r["pwr_dbw"], r["hgt_agl"]) for r in rows] == [(18.2, 45.0)]
    kinds = {s["kind"]: s["length_km"] for s in d["calc_inputs"]["segments"]}
    assert set(kinds) == {"land", "cold_sea"} and kinds["cold_sea"] > kinds["land"]  # most of the path is the Gulf of Finland
    expected = field_strength(frequency_mhz=425.825, time_percent=10, tx_effective_height_m=45.0, tx_height_agl_m=45.0,
                              rx_height_m=10, rx_clutter_height_m=10, rx_area="rural",
                              segments=[PathSegment(kinds["land"], "land"), PathSegment(kinds["cold_sea"], "cold_sea")],
                              erp_kw=10 ** ((18.2 - 30) / 10))
    assert d["value_db"] == pytest.approx(expected.field_strength_dbuv_m, abs=0.01)
    assert d["limit_db"] == 20  # T/R 25-08 A1.2.3, 400 to 470 MHz, p.6
    assert d["sensitivity"]["threshold_crossed_by"] == ["1% time"]
