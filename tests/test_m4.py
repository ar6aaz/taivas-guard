import csv
import importlib.util
import socket
from pathlib import Path

import pytest

from ific_guard.config import get_settings
from ific_guard.screening.m4 import build_band_table, classify_service, load_m4_conflict_bands
from ific_guard.screening.rules import M4NoDownlinkRule, space_rules
from ific_guard.screening.space import SpaceIssue

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "data" / "national" / "m4_bands.csv"
LOG = ROOT / "data" / "national" / "m4_classification_log.csv"


def load_tool():
    spec = importlib.util.spec_from_file_location("build_m4_bands", ROOT / "tools" / "build_m4_bands.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeReader:
    def __init__(self, **tables):
        tables.setdefault("srs_ooak", [{"version_no": 10}])
        self.tables = tables

    def table(self, name):
        return self.tables.get(name, [])


def gso_downlink(low, high, lon=25.0):
    """One GSO satellite, visible from Finland at the default longitude, transmitting in low-high MHz."""
    return SpaceIssue(FakeReader(
        notice=[dict(ntc_id=1, adm="USA", ntc_type="G", ntf_rsn="C", act_code="A", tgt_ntc_id=None,
                     prov="9.6", d_wic="2026-10-27 00:00:00")],
        geo=[dict(ntc_id=1, sat_name="SAT", long_nom=lon)],
        freq=[dict(ntc_id=1, grp_id=1, emi_rcp="E", freq_min=low, freq_max=high)],
    ))


def hits_at(low, high, **kwargs):
    rule = M4NoDownlinkRule(get_settings(), load_m4_conflict_bands(TABLE))
    return list(rule.run(gso_downlink(low, high, **kwargs)))


def row(row_id, service, low_mhz, high_mhz, table=None, station="", direction="", additional="", usage="", comment=""):
    table = table or (low_mhz, high_mhz)
    return {
        "ID": row_id, "Services_in_Finland": service, "Additional_information": additional,
        "Frequency_band_lower_limit": f"{table[0]} MHz", "Frequency_band_upper_limit": f"{table[1]} MHz",
        "Sub_band_lower_limit__Hz_": round(low_mhz * 1e6), "Sub_band_upper_limit__Hz_": round(high_mhz * 1e6),
        "Sub_band_usage": usage or service, "Class_of_station": station, "Direction": direction, "Comment": comment,
    }


def test_gso_downlink_at_3500_visible_from_finland_is_flagged():
    hits = hits_at(3480, 3520)
    assert len(hits) == 1
    assert hits[0].severity == "medium"
    band = hits[0].details["bands"][0]
    assert (band["low_mhz"], band["high_mhz"]) == (3400, 3800)
    assert "MOBILE AND FIXED" in band["m4_primary_services"]
    assert band["m4_api_row_ids"].startswith("4053")
    assert band["m4_snapshot"].startswith("taajuusjakotaulukko_")


def test_gso_downlink_at_3500_outside_the_visible_arc_is_not_flagged():
    assert hits_at(3480, 3520, lon=-120.0) == []


def test_gso_downlink_at_3900_is_judged_by_its_own_sub_band():
    assert hits_at(3880, 3920) == []  # FIXED SATELLITE (SPACE-TO-EARTH) covers 3800-4200 MHz


def test_a_carrier_across_3800_matches_only_the_lower_sub_band():
    bands = hits_at(3790, 3810)[0].details["bands"]
    assert [(b["low_mhz"], b["high_mhz"]) for b in bands] == [(3400, 3800)]


@pytest.mark.parametrize("low, high", [
    (8100, 8110),      # 8.025-8.4 GHz: fixed links with a primary EESS downlink
    (10800, 10810),    # 10.7-12.75 GHz: satellite only
    (11300, 11310),
    (12000, 12010),
    (12600, 12610),
    (18000, 18010),    # 17.8-19.7 GHz: fixed links shared with FSS downlink
    (19000, 19010),
    (19500, 19510),
])
def test_key_national_ranges_with_a_downlink_allocation_are_not_flagged(low, high):
    assert hits_at(low, high) == []


@pytest.mark.parametrize("low, high", [
    (8000, 8010),    # below the EESS sub-band: fixed links only
    (8450, 8460),    # above it
    (17770, 17780),  # M4 has only earth-to-space FSS here, and fixed links from 17755 MHz
])
def test_key_national_ranges_outside_the_downlink_sub_band_are_flagged(low, high):
    assert len(hits_at(low, high)) == 1


def test_passive_band_keeps_high_severity():
    hit = hits_at(1410, 1420)[0]
    assert hit.severity == "high"
    assert hit.details["bands"][0]["passive"] is True


def test_rule_factory_uses_the_new_table():
    rule = next(r for r in space_rules(get_settings()) if r.id == "m4-no-space-to-earth")
    assert any(b.low_mhz == 3400 and b.high_mhz == 3800 for b in rule.bands)
    assert not any(b.low_mhz == 3400 and b.high_mhz == 4200 for b in rule.bands)


def test_unresolved_direction_is_stated_in_the_case():
    details = hits_at(9500, 9510)[0].details
    assert "EARTH EXPLORATION-SATELLITE" in details["bands"][0]["space_down_unresolved"]
    assert any("gives no direction" in text for text in details["assumptions"])


def test_band_with_no_rows_states_the_assumption():
    details = hits_at(8480, 8490)[0].details
    assert details["bands"][0]["no_row_gaps_mhz"] == "8467-8500"
    assert any("no M4 sub-band row" in text for text in details["assumptions"])


@pytest.mark.parametrize("text, kind, primary, down, up", [
    ("MOBILE AND FIXED", "terrestrial", True, False, False),
    ("Amateur", "terrestrial", False, False, False),
    ("AERONAUTICAL MOBILE (OR)", "terrestrial", True, False, False),
    ("FIXED SATELLITE (SPACE-TO-EARTH)", "satellite", True, True, False),
    ("Fixed satellite (space-to-Earth)", "satellite", False, True, False),
    ("FIXED SATELLITE (EARTH-TO-SPACE)", "satellite", True, False, True),
    ("FIXED SATELLITE (EARTH-TO-SPACE, SPACE-TO-EARTH)", "satellite", True, True, True),
    ("SPACE OPERATION (SPACE-TO-EARTH) ", "satellite", True, True, False),
    ("EARTH EXPLORATION-SATELLITE (EARTH-TO-SPACE, SPACE-TO-SPACE)", "satellite", True, False, True),
    ("RADIONAVIGATION-SATELLITE (SPACE-TO-EARTH, SPACE-TO-SPACE)", "satellite", True, True, False),
    ("RADIO ASTRONOMY", "passive", True, False, False),
    ("Radio Astronomy", "passive", False, False, False),
    ("SPACE RESEARCH (PASSIVE)", "passive", True, False, False),
    ("SPACE RESEARCH (ACTIVE)", "satellite", True, False, False),
    ("INTER-SATELLITE", "satellite", True, False, False),
    ("BROADCASTING-SATELLITE", "satellite", True, True, False),
    ("MOBILE-SATELLITE", "satellite", True, None, None),
    ("WARP DRIVE", "unclassified", True, None, None),
    ("FIXED SATELLITE (SIDEWAYS)", "unclassified", True, None, None),
])
def test_service_text_classification(text, kind, primary, down, up):
    service = classify_service(text)
    assert (service.kind, service.primary, service.down, service.up) == (kind, primary, down, up)


def test_additional_information_can_mark_a_service_passive():
    assert classify_service("EARTH EXPLORATION-SATELLITE", "Passive").kind == "passive"


def test_undirected_service_takes_direction_from_its_earth_station_row():
    rows = [
        row(1, "FIXED SATELLITE", 100, 110, station="Earth station (TC)", direction="TX"),
        row(2, "FIXED SATELLITE", 100, 110, station="Space station (EC)", direction="RX"),
        row(3, "STANDARD FREQUENCY AND TIME SIGNAL-SATELLITE", 110, 120, station="Earth station (UE)", direction="RX"),
    ]
    bands, log = build_band_table(rows)
    assert (bands[0]["space_down_allocated"], bands[0]["space_up_allocated"]) == (False, True)
    assert (bands[1]["space_down_allocated"], bands[1]["space_up_allocated"]) == (True, False)
    assert log == []


def test_space_station_row_alone_does_not_prove_a_downlink():
    rows = [
        row(1, "EARTH EXPLORATION-SATELLITE", 100, 110, station="Space station (EW)", direction="TX", usage="Active sensors"),
        row(2, "EARTH EXPLORATION-SATELLITE", 100, 110, station="Space station (EW)", direction="RX", usage="Active sensors"),
    ]
    bands, log = build_band_table(rows)
    assert bands[0]["space_down_allocated"] is False
    assert bands[0]["space_down_unresolved"] == "EARTH EXPLORATION-SATELLITE"
    assert [e["category"] for e in log] == ["direction_unresolved"]


def test_unknown_service_text_is_logged_and_sets_no_flag():
    bands, log = build_band_table([row(1, "WARP DRIVE", 100, 110)])
    assert log[0]["category"] == "unclassified_service" and log[0]["service_text"] == "WARP DRIVE"
    band = bands[0]
    assert not (band["space_down_allocated"] or band["terrestrial_primary"] or band["passive_or_radio_astronomy"])


def test_table_band_splits_only_where_a_flag_changes():
    table = (3400, 4200)
    rows = [
        row(1, "Amateur", 3400, 3408, table),
        row(2, "MOBILE AND FIXED", 3400, 3800, table),
        row(3, "FIXED SATELLITE (SPACE-TO-EARTH)", 3800, 4200, table),
        row(4, "MOBILE", 3800, 4200, table),
    ]
    bands, _ = build_band_table(rows)
    assert [(b["band_low_mhz"], b["band_high_mhz"], b["space_down_allocated"]) for b in bands] == [
        ("3400", "3800", False), ("3800", "4200", True),
    ]
    assert bands[0]["api_row_ids"] == "1-2"


def test_stretch_with_no_row_keeps_terrestrial_use_but_never_a_downlink():
    table = (100, 130)
    rows = [row(1, "FIXED", 105, 110, table), row(2, "FIXED SATELLITE (SPACE-TO-EARTH)", 120, 130, table)]
    bands, _ = build_band_table(rows)
    assert [(b["band_low_mhz"], b["band_high_mhz"], b["space_down_allocated"], b["terrestrial_primary"], b["no_row_gaps_mhz"])
            for b in bands] == [
        ("100", "120", False, True, "100-105 110-120"),
        ("120", "130", True, False, ""),
    ]


def test_build_from_snapshot_needs_no_network_and_reproduces_the_committed_table(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the build tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    tool = load_tool()
    summary = tool.build(tool.newest_snapshot(TABLE.parent), tmp_path)
    assert summary["source_rows"] == 5917
    assert (tmp_path / "m4_bands.csv").read_bytes() == TABLE.read_bytes()
    assert (tmp_path / "m4_classification_log.csv").read_bytes() == LOG.read_bytes()


def test_snapshot_that_does_not_match_its_checksum_is_refused(tmp_path):
    tool = load_tool()
    snapshot = tmp_path / "taajuusjakotaulukko_2026-01-01.json"
    snapshot.write_text('{"value": []}')
    snapshot.with_suffix(".meta.json").write_text('{"sha256": "not-the-hash"}')
    with pytest.raises(SystemExit, match="checksum"):
        tool.build(snapshot, tmp_path)


def test_every_service_in_the_snapshot_is_classified():
    with LOG.open(newline="", encoding="utf-8") as f:
        assert not [e for e in csv.DictReader(f) if e["category"] == "unclassified_service"]


def test_old_table_is_kept_and_still_loads():
    bands = load_m4_conflict_bands(ROOT / "generetated-files" / "m4_satellite_filter.csv")
    assert not any(b.low_mhz == 3400 for b in bands)  # the false negative this table replaces
