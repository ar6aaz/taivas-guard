import pytest

from ific_guard import AnalysisStore
from ific_guard.cli import main, _write_csv
from ific_guard.readers import read_mdb


def test_real_mdb_adapter_contract_and_empty_table(monkeypatch, tmp_path):
    import access_parser
    class Parser:
        def __init__(self, path):
            self.catalog = {"NOTICE": 1, "grp": 2, "provn": 3, "irrelevant": 4}

        def parse_table(self, name):
            return {"ntc_id": [1, 2], "adm": ["FIN", "S"]} if name == "NOTICE" else {}
    monkeypatch.setattr(access_parser, "AccessParser", Parser)
    result = {name: list(rows) for name, rows in read_mdb(tmp_path / "sample.mdb", "space")}
    assert result == {"notice": [{"ntc_id": 1, "adm": "FIN"}, {"ntc_id": 2, "adm": "S"}], "grp": [], "provn": []}


def test_mdb_adapter_rejects_inconsistent_columns(monkeypatch, tmp_path):
    import access_parser
    class Parser:
        def __init__(self, path):
            self.catalog = dict.fromkeys(("notice", "grp", "provn"))

        def parse_table(self, name):
            return {"id": [1, 2], "value": [1]}
    monkeypatch.setattr(access_parser, "AccessParser", Parser)
    with pytest.raises(ValueError, match="column lengths"):
        list(read_mdb(tmp_path / "sample.mdb", "space"))


def test_terrestrial_land_mobile_scope_and_geo_change(tmp_path):
    path = tmp_path / "issue.mdb"
    path.write_bytes(b"decoded by fixture reader")
    stations = tmp_path / "stations.csv"
    stations.write_text("station_id,f_low_mhz,f_high_mhz\nA,153,154\n", encoding="utf-8")
    tables = {
        "fxm_terra": [{"terrakey": 1, "geo_key": 10, "adm": "RUS", "stn_cls": "FB", "freq_min": 153, "freq_max": 154},
                      {"terrakey": 2, "geo_key": 10, "adm": "FIN", "stn_cls": "FB", "freq_min": 153, "freq_max": 154},
                      {"terrakey": 3, "geo_key": 10, "adm": "RUS", "stn_cls": "FX", "freq_min": 153, "freq_max": 154}],
        "fxm_geo": [{"geo_key": 10, "lat_dec": 60, "long_dec": 29}],
        "fxm_ant": [{"terrakey": 1, "pwr_dbw": 10}],
    }
    with AnalysisStore(":memory:") as store:
        before = store.import_ific(path, domain="terrestrial", issue_no=3076,
                                   reader=lambda p,d: tables.items(), reader_version="fixture")
        national = store.import_stations(stations)
        assert [r.assignment_id for r in store.screen(before.source_key, national.source_key)] == ["1"]
        tables["fxm_geo"][0]["lat_dec"] = 61
        after = store.import_ific(path, domain="terrestrial", issue_no=3077,
                                  reader=lambda p,d: tables.items(), reader_version="fixture")
        assert dict(store.changes(before.source_key, after.source_key)) == {"1": "modified", "2": "modified", "3": "modified"}


def test_import_source_changes_mid_parse_roll_back(tmp_path):
    source = tmp_path / "issue.mdb"
    source.write_bytes(b"initial")
    def changing_reader(path, domain):
        yield "notice", [{"ntc_id": 1, "ntc_type": "N"}]
        yield "grp", [{"ntc_id": 1, "grp_id": 1, "freq_min": 100, "freq_max": 101}]
        yield "provn", []
        path.write_bytes(b"changed during parsing")
    with AnalysisStore(":memory:") as store, pytest.raises(ValueError, match="changed during import"):
        try:
            store.import_ific(source, domain="space", issue_no=3074, reader=changing_reader, reader_version="fixture")
        finally:
            assert store.connection.execute("SELECT count(*) FROM ific_raw").fetchone()[0] == 0
            assert store.connection.execute("SELECT count(*) FROM ific_assignment").fetchone()[0] == 0


def test_atomic_csv_failure_does_not_replace_previous_output(tmp_path):
    output = tmp_path / "candidates.csv"
    output.write_text("previous complete result", encoding="utf-8")
    def broken():
        yield {"id": "one"}
        raise ValueError("failed halfway")
    with pytest.raises(ValueError, match="halfway"):
        _write_csv(output, ["id"], broken())
    assert output.read_text() == "previous complete result"
    assert not list(tmp_path.glob("*.tmp"))


def test_explicit_terrestrial_suppression_is_not_screened_as_new_interference(tmp_path):
    path = tmp_path / "issue.mdb"
    path.write_bytes(b"fixture")
    national = tmp_path / "stations.csv"
    national.write_text("station_id,f_low_mhz,f_high_mhz\nA,153,154\n", encoding="utf-8")
    tables = {
        "fxm_terra": [{"terrakey": 1, "geo_key": 1, "adm": "RUS", "stn_cls": "FB", "freq_min": 153, "freq_max": 154, "intent": "ADD"}],
        "fxm_geo": [{"geo_key": 1, "lat_dec": 60, "long_dec": 29}],
    }
    with AnalysisStore(":memory:") as store:
        old = store.import_ific(path, domain="terrestrial", issue_no=3076, reader=lambda p,d:tables.items(), reader_version="fixture")
        tables["fxm_terra"][0]["intent"] = "SUPPRESS"
        new = store.import_ific(path, domain="terrestrial", issue_no=3077, reader=lambda p,d:tables.items(), reader_version="fixture")
        stations = store.import_stations(national)
        assert list(store.changes(old.source_key, new.source_key)) == [("1", "withdrawn")]
        assert list(store.screen(new.source_key, stations.source_key)) == []


def test_cli_import_cache_and_validation_errors(tmp_path, capsys):
    path = tmp_path / "stations.csv"
    path.write_text("station_id,f_low_mhz,f_high_mhz\n001,100,101\n", encoding="utf-8")
    args = ["--db", str(tmp_path / "cache.duckdb"), "import-stations", str(path)]
    assert main(args) == 0
    assert '"cache_hit": false' in capsys.readouterr().out
    assert main(args) == 0
    assert '"cache_hit": true' in capsys.readouterr().out
    assert main(["--db", str(tmp_path / "cache.duckdb"), "screen", "--source", "missing", "--stations", "missing", "--output", str(tmp_path / "out.csv")]) == 1
    assert "Unknown source key" in capsys.readouterr().err
    assert not (tmp_path / "out.csv").exists()
