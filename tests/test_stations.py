from collections import Counter
from dataclasses import replace

import pytest
from sqlalchemy import select

from ific_guard.config import get_settings
from ific_guard.db import session_scope
from ific_guard.models import Artifact, Case
from ific_guard.screening import stations as st
from ific_guard.screening.engine import screen_artifact
from ific_guard.screening.rule_stations import StationOverlapRule, station_overlap_rule
from ific_guard.screening.rules import space_rules
from ific_guard.screening.space import SpaceIssue
from ific_guard.screening.stations import Station, load_stations, parse_bandwidth_mhz, parse_frequency_mhz
from tests.test_screening import FakeReader, freq, notice


@pytest.fixture(scope="module")
def stations():
    return load_stations()


@pytest.fixture(scope="module")
def by_row(stations):
    return {s.row: s for s in stations}


def flagged(stations, flag):
    return [s for s in stations if flag in s.quality_flags]


def test_loader_returns_873_records_starting_after_both_header_rows(stations):
    assert len(stations) == 873
    assert stations[0].row == 13 and stations[0].station_no == 5931


def test_exact_duplicate_row_is_removed_and_recorded(by_row, stations):
    assert 21 not in by_row
    assert [s.row for s in flagged(stations, st.DUPLICATE_REMOVED)] == [20]


def test_reused_station_number_keeps_both_rows_under_distinct_keys(by_row, stations):
    assert (by_row[363].key, by_row[364].key) == ("626855@R363", "626855@R364")
    assert by_row[363].lat_deg != by_row[364].lat_deg
    assert [s.row for s in flagged(stations, st.STATION_NUMBER_REUSED)] == [363, 364]
    assert len({s.key for s in stations}) == 873


def test_empty_text_and_none_both_read_as_blank(by_row):
    assert by_row[13].ant_gain_db is None  # the sheet holds ''
    assert by_row[363].ant_gain_db is None  # the sheet holds None
    assert by_row[13].ant_height_asl_m is None and by_row[108].mast_height_m is None


def test_numbers_stored_as_text_are_read(by_row):
    assert by_row[13].lon_deg == 24.64014 and by_row[13].lat_deg == 60.17766
    assert by_row[44].power == 0.5
    assert by_row[20].line_loss_db == 0.0


@pytest.mark.parametrize("text,mhz", [("148.41875 MHz", 148.41875), ("6120.000 kHz", 6.12), ("25.9 GHz", 25900.0), ("8211 MHz", 8211.0), ("7,5 MHz", 7.5)])
def test_frequency_units(text, mhz):
    assert parse_frequency_mhz(text) == mhz


@pytest.mark.parametrize("text", ["", None, "   ", "abc", "100", "5 THz"])
def test_unreadable_frequency_is_none(text):
    assert parse_frequency_mhz(text) is None


@pytest.mark.parametrize(
    "text,mhz",
    [("300.000 kHz", 0.3), ("8000.000 kHz", 8.0), ("0.1 GHz", 100.0), ("8M00", 8.0), ("10k0", 0.01),
     ("5000k", 5.0), ("7,6k", 0.0076), ("1,4M", 1.4), ("0,1G", 100.0), ("5 MHz", 5.0)],
)
def test_bandwidth_forms(text, mhz):
    assert parse_bandwidth_mhz(text) == mhz


@pytest.mark.parametrize("text", [None, "", "abc", "1,4M00"])
def test_unreadable_bandwidth_is_none(text):
    assert parse_bandwidth_mhz(text) is None


def test_real_rows_cover_each_frequency_unit_and_every_row_parses(by_row, stations):
    assert by_row[165].rx_mhz == 6.12  # kHz in the sheet
    assert by_row[364].rx_mhz == 148.41875  # MHz
    assert by_row[832].rx_mhz == 25900.0  # GHz
    assert not flagged(stations, st.BAD_FREQUENCY) and not flagged(stations, st.NO_BANDWIDTH)


def test_blank_channel_bandwidth_falls_back_to_transmission_bandwidth(by_row, stations):
    assert [s.row for s in flagged(stations, st.NO_CHANNEL_BW)] == [139, 148]
    assert by_row[139].channel_bw_mhz is None and by_row[139].bandwidth_mhz == 8.0
    low, high = by_row[139].rx_band_mhz
    assert high - low == pytest.approx(8.0)


def test_channel_bandwidth_wins_when_the_two_differ_and_is_flagged(by_row, stations):
    assert len(flagged(stations, st.BW_MISMATCH)) == 17
    assert st.BW_MISMATCH in by_row[199].quality_flags
    assert (by_row[199].channel_bw_mhz, by_row[199].transmission_bw_mhz) == (2.5, 118.6)
    assert by_row[199].rx_band_mhz == (2274.75, 2277.25)


def test_rows_with_no_frequency_are_kept_and_flagged(by_row, stations):
    no_rx, no_tx = flagged(stations, st.NO_RX), flagged(stations, st.NO_TX)
    assert len(no_rx) == 61 and all(s.rx_mhz is None and s.rx_band_mhz is None for s in no_rx)
    assert [s.row for s in no_tx] == [108, 109, 110] and all(s.tx_mhz is None for s in no_tx)
    assert by_row[363].tx_mhz == 107.5 and by_row[363].rx_band_mhz is None


def test_protected_band_counts_on_the_real_sheet(stations):
    sides = Counter(tuple(b.side for b in s.protected_bands) for s in stations)
    assert sides == {("rx", "tx"): 572, ("tx",): 58, ("both",): 240, (): 3}
    assert sum(1 for s in stations if not s.protected_bands and s.rx_mhz is None and s.tx_mhz is None) == 3


def test_tx_only_row_is_protected_at_its_tx_frequency(by_row):
    bands = by_row[363].protected_bands
    assert [b.side for b in bands] == ["tx"]
    assert bands[0].low_mhz < 107.5 < bands[0].high_mhz


def test_equal_rx_and_tx_are_one_band():
    bands = station(tx_mhz=2240.0).protected_bands
    assert [(b.side, b.low_mhz, b.high_mhz) for b in bands] == [("both", 2237.5, 2242.5)]


def test_different_rx_and_tx_are_two_labelled_bands():
    bands = station(tx_mhz=2300.0).protected_bands
    assert [(b.side, b.low_mhz, b.high_mhz) for b in bands] == [("rx", 2237.5, 2242.5), ("tx", 2297.5, 2302.5)]


def test_mvv_gain_is_discarded(stations):
    mvv = [s for s in stations if s.licence_class == "MVV"]
    assert len(mvv) == 63 and flagged(stations, st.MVV_GAIN) == mvv
    assert all(s.ant_gain_db is None for s in mvv)
    assert any(s.ant_gain_db is not None for s in stations if s.licence_class != "MVV")


def test_implausible_power_is_only_the_no_class_rows_and_keeps_its_value(stations):
    bad = flagged(stations, st.POWER_IMPLAUSIBLE)
    assert len(bad) == 67 and {s.licence_class for s in bad} == {None}
    watts = sorted(s.power for s in bad)
    assert 133e6 < watts[0] < 134e6 and 9.4e9 < watts[-1] < 9.5e9


def test_negative_antenna_height_is_kept_and_flagged(by_row, stations):
    assert [s.row for s in flagged(stations, st.NEGATIVE_HEIGHT)] == [108]
    assert by_row[108].ant_height_agl_m == -28


def test_changed_layout_is_refused(tmp_path):
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["not the expected sheet"])
    path = tmp_path / "other.xlsx"
    workbook.save(path)
    with pytest.raises(ValueError, match="layout has changed"):
        load_stations(path)


# The rule


def station(row=100, rx=2240.0, bw=5.0, cls="FX", number=None, tx_mhz=None, **kw):
    return Station(
        row=row, station_no=number or row * 10, licence_class=cls, lon_deg=25.0, lat_deg=62.0, ground_height_m=None,
        mast_height_m=None, ant_height_agl_m=None, ant_height_asl_m=None, ant_gain_db=None, tx_mhz=tx_mhz, rx_mhz=rx,
        channel_bw_mhz=bw, transmission_bw_mhz=bw, transmission_class=None, radiation_direction_deg=None,
        polarisation=None, power=None, power_unit=None, power_itu=None, line_loss_db=None, directivity=None, **kw,
    )


def run(protected, **tables):
    return list(StationOverlapRule(get_settings(), protected).run(SpaceIssue(FakeReader(**tables))))


def gso(*ntc_ids, lon=25.0):
    return [dict(ntc_id=i, sat_name=f"SAT{i}", long_nom=lon) for i in ntc_ids]


def test_downlink_on_a_receive_band_uses_the_details_contract():
    hits = run([station()], notice=[notice(1)], geo=gso(1), freq=[freq(1, 2230, 2250)])
    assert len(hits) == 1
    hit = hits[0]
    assert hit.rule_id == "station-rx-overlap" and hit.severity == "medium" and hit.notice_ids == [1]
    assert "1000@R100" in hit.details["station_ids"]
    assert "1 protected station(s): FX 1" in hit.title
    details = hit.details
    assert details["method"] == "screening"
    assert details["source"] == {"domain": "space", "notice_ids": ["1"]}
    assert details["assumptions"] and not {"value_db", "limit_db", "margin_db"} & set(details)
    assert details["bands"] == [
        {"low_mhz": 2237.5, "high_mhz": 2242.5, "classes": {"FX": 1}, "station_ids": ["1000@R100"], "sides": {"rx": 1}, "group_ids": [1]}
    ]


@pytest.mark.parametrize(
    "tables",
    [
        dict(notice=[notice(1)], geo=gso(1), freq=[freq(1, 2230, 2250, emi="R")]),  # uplink
        dict(notice=[notice(1)], geo=gso(1), freq=[freq(1, 2242.5, 2250)]),  # touches the band edge only
        dict(notice=[notice(1)], geo=gso(1, lon=140.0), freq=[freq(1, 2230, 2250)]),  # beyond the visible arc
        dict(notice=[notice(1, ntc_type="N")], orbit=[dict(ntc_id=1, inclin_ang=30.0)], freq=[freq(1, 2230, 2250)]),  # never reaches Finland
        dict(notice=[notice(1, ntc_type="S")], freq=[freq(1, 2230, 2250)]),  # earth station
        dict(notice=[notice(1, reason="N")], geo=gso(1), freq=[freq(1, 2230, 2250)]),  # out of scope
        dict(notice=[notice(1, act="S")], geo=gso(1), freq=[freq(1, 2230, 2250)]),  # suppression
        dict(notice=[notice(1)], geo=gso(1), freq=[freq(1, None, None)]),  # no frequency
    ],
)
def test_no_case(tables):
    assert run([station()], **tables) == []


def test_retrograde_orbit_counts_as_visible():
    tables = dict(notice=[notice(1, ntc_type="N")], orbit=[dict(ntc_id=1, inclin_ang=98.0)], freq=[freq(1, 2230, 2250)])
    assert len(run([station()], **tables)) == 1


def test_one_case_per_family_with_contiguous_bands_joined():
    near = [station(row=100, rx=2240.0), station(row=101, rx=2243.0), station(row=102, rx=18000.0, bw=10.0, cls=None)]
    hits = run(
        near,
        notice=[notice(1), notice(9, act="M", target=1), notice(2)],
        geo=gso(1, 9, 2),
        freq=[freq(1, 2230, 2250, grp=11), freq(9, 17990, 18010, grp=12), freq(2, 2230, 2250, grp=13)],
    )
    assert sorted(h.family_id for h in hits) == [1, 2]
    family = next(h for h in hits if h.family_id == 1)
    assert family.notice_ids == [1, 9]
    assert family.details["classes"] == {"FX": 2, "no class": 1}
    assert [(b["low_mhz"], b["high_mhz"], b["station_ids"], b["group_ids"]) for b in family.details["bands"]] == [
        (2237.5, 2245.5, ["1000@R100", "1010@R101"], [11]),
        (17995.0, 18005.0, ["1020@R102"], [12]),
    ]


def test_invisible_notice_in_a_family_does_not_contribute():
    hits = run(
        [station(), station(row=101, rx=18000.0)],
        notice=[notice(1), notice(9, act="M", target=1)],
        geo=gso(1) + gso(9, lon=140.0),
        freq=[freq(1, 2230, 2250), freq(9, 17990, 18010)],
    )
    assert hits[0].details["station_ids"] == ["1000@R100"]


def test_station_with_no_frequency_is_skipped_and_the_count_is_stated():
    neither = station(row=101, rx=None, quality_flags=(st.NO_RX, st.NO_TX))
    hits = run([station(), neither], notice=[notice(1)], geo=gso(1), freq=[freq(1, 2230, 2250)])
    assert hits[0].details["station_ids"] == ["1000@R100"]
    assert hits[0].details["assumptions"][0].startswith("1 of 2 stations have no usable RX or TX frequency")


def test_rx_match_is_labelled_rx():
    hits = run([station(tx_mhz=2300.0)], notice=[notice(1)], geo=gso(1), freq=[freq(1, 2230, 2250)])
    assert hits[0].details["matched_on"] == {"rx": ["1000@R100"]}
    assert "TX side only" not in hits[0].title


def test_tx_match_is_found_and_labelled_when_rx_does_not_overlap():
    hits = run([station(tx_mhz=2300.0)], notice=[notice(1)], geo=gso(1), freq=[freq(1, 2290, 2310)])
    assert hits[0].details["matched_on"] == {"tx": ["1000@R100"]}
    assert hits[0].title.endswith("; TX side only")
    assert hits[0].details["bands"][0]["sides"] == {"tx": 1}


def test_tx_only_row_produces_a_case():
    tx_only = station(rx=None, tx_mhz=2300.0, quality_flags=(st.NO_RX,))
    hits = run([tx_only], notice=[notice(1)], geo=gso(1), freq=[freq(1, 2290, 2310)])
    assert hits[0].details["matched_on"] == {"tx": ["1000@R100"]}


def test_equal_rx_and_tx_match_once_as_both():
    hits = run([station(tx_mhz=2240.0)], notice=[notice(1)], geo=gso(1), freq=[freq(1, 2230, 2250)])
    assert hits[0].details["matched_on"] == {"both": ["1000@R100"]}
    assert hits[0].details["bands"][0]["sides"] == {"both": 1}


def test_station_matching_on_both_sides_counts_once():
    hits = run([station(tx_mhz=2300.0)], notice=[notice(1)], geo=gso(1), freq=[freq(1, 2230, 2310)])
    details = hits[0].details
    assert details["station_ids"] == ["1000@R100"] and details["classes"] == {"FX": 1}
    assert details["matched_on"] == {"rx": ["1000@R100"], "tx": ["1000@R100"]}
    assert "1 protected station(s)" in hits[0].title and [b["sides"] for b in details["bands"]] == [{"rx": 1}, {"tx": 1}]


def test_bandwidth_quality_flags_reach_the_case():
    mismatch = replace(station(), quality_flags=(st.BW_MISMATCH,))
    hits = run([mismatch], notice=[notice(1)], geo=gso(1), freq=[freq(1, 2230, 2250)])
    assert hits[0].details["station_band_flags"] == {st.BW_MISMATCH: ["1000@R100"]}


def test_rule_is_enabled_and_records_the_real_dataset():
    rule = next(r for r in space_rules(get_settings()) if r.id == "station-rx-overlap")
    assert rule.total == 873 and len(rule.protected) == 870
    hits = list(rule.run(SpaceIssue(FakeReader(notice=[notice(1)], geo=gso(1), freq=[freq(1, 2219, 2246)]))))
    assert hits[0].details["assumptions"][0].startswith("3 of 873 stations")
    assert hits[0].details["station_dataset"]["file"] == st.STATIONS_PATH.name
    assert len(hits[0].details["station_dataset"]["sha256"]) == 64
    assert hits[0].details["classes"]["FX"] > 100


def test_case_saves_through_the_engine_and_a_rerun_changes_nothing(tmp_path):
    with session_scope() as s:
        artifact = Artifact(domain="space", issue_no=3083, source_url="https://www.itu.int/t.zip", sha256="a" * 64,
                            size_bytes=1, mdb_path=str(tmp_path / "x.mdb"))
        s.add(artifact)
        s.flush()
        artifact_id = artifact.id
    reader = FakeReader(notice=[notice(1)], geo=gso(1), freq=[freq(1, 2219, 2246)])
    rule = station_overlap_rule(get_settings())
    assert screen_artifact(artifact_id, reader=reader, rules=[rule])["opened"] == 1
    again = screen_artifact(artifact_id, reader=reader, rules=[rule])
    assert (again["opened"], again["changed"]) == (0, 0)
    with session_scope() as s:
        case = s.scalars(select(Case)).one()
        assert case.rule_id == "station-rx-overlap" and case.details["method"] == "screening"
        assert case.details["station_ids"]
