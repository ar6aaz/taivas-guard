from copy import deepcopy

import pytest

from ific_guard import AnalysisStore


def space_tables():
    return {
        "notice": [{"ntc_id": 1, "ntc_type": "G", "adm": "S"},
                   {"ntc_id": 2, "ntc_type": "G", "adm": "USA"},
                   {"ntc_id": 3, "ntc_type": "N", "adm": "F"}],
        "grp": [{"ntc_id": 1, "grp_id": 11, "freq_min": 100, "freq_max": 110, "emi_rcp": "E"},
                {"ntc_id": 2, "grp_id": 21, "freq_min": 900, "freq_max": 910, "emi_rcp": "R"},
                {"ntc_id": 3, "grp_id": 31, "freq_min": None, "freq_max": None, "emi_rcp": "E"}],
        "provn": [{"grp_id": 21, "adm": "FIN"}],
        "geo": [{"ntc_id": 1, "long_nom": 25}, {"ntc_id": 2, "long_nom": -150}],
        "emiss": [{"grp_id": 11, "pwr_ds_max": -50}],
    }


def import_fixture(store, path, tables, issue=3074, version="fixture-1"):
    return store.import_ific(path, domain="space", issue_no=issue,
                             reader=lambda p, d: tables.items(), reader_version=version)


@pytest.fixture
def inputs(tmp_path):
    mdb = tmp_path / "source.mdb"
    mdb.write_bytes(b"fixture content; decoded through the injected reader")
    stations = tmp_path / "stations.csv"
    stations.write_text("station_id,f_low_mhz,f_high_mhz,latitude_deg,longitude_deg\nA,105,106,60,25\nA,108,109,61,26\nB,110,120,62,27\n", encoding="utf-8")
    return mdb, stations


def test_import_cache_survives_reopen_and_never_calls_reader(inputs, tmp_path):
    mdb, stations = inputs
    database = tmp_path / "cache.duckdb"
    with AnalysisStore(database) as store:
        first = import_fixture(store, mdb, space_tables())
        reference = store.import_stations(stations)
        assert not first.cache_hit and first.table_counts["grp"] == 3
    def fail_reader(*args):
        raise AssertionError("A cache hit must not decode MDB again")
    with AnalysisStore(database) as store:
        cached = store.import_ific(mdb, domain="space", issue_no=3074, reader=fail_reader, reader_version="fixture-1")
        assert cached.cache_hit and cached.source_key == first.source_key
        assert store.import_stations(stations).cache_hit
        assert store.station_stats(reference.source_key)["records"] == 3


def test_screen_preserves_fin_and_missing_frequency_and_retrieves_duplicate_ids(inputs):
    mdb, stations = inputs
    with AnalysisStore(":memory:") as store:
        issue = import_fixture(store, mdb, space_tables())
        national = store.import_stations(stations)
        candidates = {c.assignment_id: c for c in store.screen(issue.source_key, national.source_key, batch_size=1)}
        assert candidates["1:11"].matched_assignment_count == 2
        assert candidates["2:21"].fin_listed and candidates["2:21"].matched_assignment_count == 0
        assert candidates["3:31"].matched_assignment_count is None
        assert "unknown_assignment_frequency" in candidates["3:31"].review_reasons
        matches = list(store.matches(issue.source_key, national.source_key, "1:11"))
        assert [r[1] for r in matches] == ["A", "A"]
        adjacent = list(store.matches(issue.source_key, national.source_key, "1:11", gap_mhz=.1))
        assert len(adjacent) == 3


def test_cache_identity_includes_parser_version_issue_and_source_content(inputs):
    mdb, _ = inputs
    with AnalysisStore(":memory:") as store:
        a = import_fixture(store, mdb, space_tables())
        b = import_fixture(store, mdb, space_tables(), version="fixture-2")
        c = import_fixture(store, mdb, space_tables(), issue=3076)
        mdb.write_bytes(b"changed source bytes")
        d = import_fixture(store, mdb, space_tables())
        assert len({r.source_key for r in (a, b, c, d)}) == 4


def test_failed_import_rolls_back_every_table_and_can_be_retried(inputs):
    mdb, _ = inputs
    def broken(*args):
        yield "notice", [{"ntc_id": 1}]
        raise ValueError("parser failed halfway")
    with AnalysisStore(":memory:") as store:
        with pytest.raises(ValueError, match="halfway"):
            store.import_ific(mdb, domain="space", issue_no=3074, reader=broken, reader_version="fixture-1")
        assert store.connection.execute("SELECT count(*) FROM ific_raw").fetchone()[0] == 0
        assert store.connection.execute("SELECT count(*) FROM ific_source").fetchone()[0] == 0
        assert not import_fixture(store, mdb, space_tables()).cache_hit


def test_missing_required_table_fails_instead_of_assuming_fin_not_listed(inputs):
    mdb, _ = inputs
    tables = space_tables()
    del tables["provn"]
    with AnalysisStore(":memory:") as store, pytest.raises(ValueError, match="required"):
        import_fixture(store, mdb, tables)


def test_new_modified_withdrawn_includes_emission_only_changes(inputs):
    mdb, _ = inputs
    before = space_tables()
    after = deepcopy(before)
    after["emiss"][0]["pwr_ds_max"] = -40
    after["grp"] = [r for r in after["grp"] if r["grp_id"] != 21]
    after["grp"].append({"ntc_id": 3, "grp_id": 32, "freq_min": 120, "freq_max": 130, "emi_rcp": "E"})
    with AnalysisStore(":memory:") as store:
        a = import_fixture(store, mdb, before)
        b = import_fixture(store, mdb, after, issue=3076)
        changes = dict(store.changes(a.source_key, b.source_key, complete_snapshots=True))
        assert changes["1:11"] == "modified"
        assert changes["2:21"] == "withdrawn"
        assert changes["3:32"] == "new"


def test_publication_absence_is_not_mistaken_for_withdrawal(inputs):
    mdb, _ = inputs
    before = space_tables()
    after = deepcopy(before)
    after["grp"] = [r for r in after["grp"] if r["grp_id"] != 21]
    after["grp"].append({"ntc_id": 3, "grp_id": 32, "freq_min": 120, "freq_max": 130, "emi_rcp": "E"})
    with AnalysisStore(":memory:") as store:
        a = import_fixture(store, mdb, before)
        b = import_fixture(store, mdb, after, issue=3076)
        changes = dict(store.changes(a.source_key, b.source_key))
        assert changes["2:21"] == "not_republished"
        assert changes["3:32"] == "published"
        assert list(store.changes(a.source_key, a.source_key)) == []


def test_complete_snapshots_do_not_replay_historical_add_actions(inputs):
    mdb, _ = inputs
    tables = space_tables()
    for row in tables["notice"]:
        row["act_code"] = "A"
    with AnalysisStore(":memory:") as store:
        a = import_fixture(store, mdb, tables)
        b = import_fixture(store, mdb, tables, issue=3076)
        assert list(store.changes(a.source_key, b.source_key, complete_snapshots=True)) == []


def test_invalid_space_metadata_is_reviewed_instead_of_dropped(inputs):
    mdb, stations = inputs
    tables = space_tables()
    tables["notice"][0]["ntc_type"] = "?"
    tables["grp"][0]["emi_rcp"] = "?"
    with AnalysisStore(":memory:") as store:
        issue = import_fixture(store, mdb, tables)
        national = store.import_stations(stations)
        rows = {c.assignment_id: c for c in store.screen(issue.source_key, national.source_key)}
        assert "incomplete_space_metadata" in rows["1:11"].review_reasons


def test_read_only_analysis_and_parquet_export(inputs, tmp_path):
    mdb, stations = inputs
    database = tmp_path / "cache.duckdb"
    with AnalysisStore(database) as store:
        issue = import_fixture(store, mdb, space_tables())
        national = store.import_stations(stations)
    with AnalysisStore(database, read_only=True) as first, AnalysisStore(database, read_only=True) as second:
        a = list(first.screen(issue.source_key, national.source_key))
        assert list(second.screen(issue.source_key, national.source_key)) == a
        first.export_assignments(issue.source_key, tmp_path / "readonly-export.parquet")


def test_unknown_national_frequency_never_looks_like_zero_concern(inputs):
    mdb, stations = inputs
    stations.write_text("station_id,f_low_mhz,f_high_mhz\nUNKNOWN,,\n", encoding="utf-8")
    with AnalysisStore(":memory:") as store:
        issue = import_fixture(store, mdb, space_tables())
        national = store.import_stations(stations)
        rows = list(store.screen(issue.source_key, national.source_key))
        assert store.station_stats(national.source_key)["unknown_frequency_records"] == 1
        assert all("incomplete_national_frequencies" in row.review_reasons for row in rows)


def test_invisible_gso_is_excluded_only_if_fin_is_not_listed(inputs):
    mdb, stations = inputs
    tables = space_tables()
    tables["geo"][0]["long_nom"] = -150
    with AnalysisStore(":memory:") as store:
        issue = import_fixture(store, mdb, tables)
        national = store.import_stations(stations)
        ids = {c.assignment_id for c in store.screen(issue.source_key, national.source_key)}
        assert "1:11" not in ids and "2:21" in ids


def test_point_group_expands_using_documented_space_bandwidth(inputs):
    mdb, stations = inputs
    tables = space_tables()
    tables["grp"][0].update(freq_min=105, freq_max=105, bdwdth=20)
    with AnalysisStore(":memory:") as store:
        issue = import_fixture(store, mdb, tables)
        row = store.connection.execute("SELECT f_low_mhz,f_high_mhz FROM ific_assignment WHERE assignment_id='1:11'").fetchone()
        assert row == pytest.approx((104.99, 105.01))


def test_parquet_preserves_ids_and_types(inputs, tmp_path):
    mdb, _ = inputs
    with AnalysisStore(":memory:") as store:
        issue = import_fixture(store, mdb, space_tables())
        output = tmp_path / "assignments.parquet"
        store.export_assignments(issue.source_key, output)
        rows = store.connection.execute("SELECT assignment_id,f_low_mhz FROM read_parquet(?) ORDER BY assignment_id", [str(output)]).fetchall()
        assert rows == [("1:11", 100.0), ("2:21", 900.0), ("3:31", None)]


def test_xlsx_original_layout_units_missing_values_and_duplicates(tmp_path):
    import openpyxl
    path = tmp_path / "reference.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Frequencies of interest"
    sheet.append([])
    for _ in range(9):
        sheet.append([])
    sheet.append(["Station number", "RX frequency", "Channel bandwidth", "Class of licence", "Latitude", "Longitude"])
    sheet.append(["Finnish headers"])
    sheet.append(["001", "1,05 GHz", "200 kHz", "FX", 60, 25])
    sheet.append(["001", "1050 MHz", "200 kHz", "FX", 60, 25])
    sheet.append(["BC", None, "200 kHz", "BC", 60, 25])
    book.save(path)
    with AnalysisStore(":memory:") as store:
        imported = store.import_stations(path)
        assert store.station_stats(imported.source_key) == {"records": 3, "usable_receiver_assignments": 2, "unknown_frequency_records": 0}
        row = store.connection.execute("SELECT station_id,f_low_mhz,f_high_mhz FROM ific_station WHERE usable ORDER BY row_no LIMIT 1").fetchone()
        assert row[0] == "001"
        assert row[1:] == pytest.approx((1049.9, 1050.1))
