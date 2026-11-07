import math

import pytest

from ific_guard import AnalysisStore
from ific_guard.store import _distance_lower_bound_km, _gso_visible


def test_rectangle_distance_and_unknown_coordinates():
    assert _distance_lower_bound_km(60, 25) == pytest.approx(0, abs=.001)
    assert _distance_lower_bound_km(58, 25) == pytest.approx(6371 * math.pi / 180)
    assert _distance_lower_bound_km(60, 130) > 1000
    assert _distance_lower_bound_km(None, 25) is None
    assert _distance_lower_bound_km(91, 25) is None
    assert _gso_visible(25) is True
    assert _gso_visible(-150) is False
    assert _gso_visible(float('nan')) is None


def test_explicit_radius_excludes_far_stations_but_keeps_unknown_location(tmp_path):
    path = tmp_path / "issue.mdb"
    path.write_bytes(b"fixture")
    national = tmp_path / "stations.csv"
    national.write_text("station_id,f_low_mhz,f_high_mhz\nA,153,154\n", encoding="utf-8")
    tables = {
        "fxm_terra": [{"terrakey": i, "geo_key": i, "adm": "RUS", "stn_cls": "FB", "freq_min": 153, "freq_max": 154} for i in (1, 2, 3)],
        "fxm_geo": [{"geo_key": 1, "lat_dec": 60, "long_dec": 29},
                    {"geo_key": 2, "lat_dec": 45, "long_dec": 130},
                    {"geo_key": 3, "lat_dec": None, "long_dec": None}],
    }
    with AnalysisStore(":memory:") as store:
        issue = store.import_ific(path, domain="terrestrial", issue_no=3076, reader=lambda p,d: tables.items(), reader_version="fixture")
        stations = store.import_stations(national)
        assert len(list(store.screen(issue.source_key, stations.source_key))) == 3
        rows = {r.assignment_id: r for r in store.screen(issue.source_key, stations.source_key, max_terrestrial_distance_km=1000)}
        assert set(rows) == {"1", "3"}
        assert "unknown_terrestrial_location" in rows["3"].review_reasons
        assert rows["1"].max_terrestrial_distance_km == 1000
        with pytest.raises(ValueError):
            list(store.screen(issue.source_key, stations.source_key, max_terrestrial_distance_km=-1))
