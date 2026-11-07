from ific_guard.terrestrial.adapter import (
    TerrestrialIssue,
    distance_to_finland,
    filter_near_finland,
)
from tests.test_terrestrial import issue


def test_filter_uses_configured_administrations_and_band_limit():
    current = issue(
        3076,
        [
            (1, "RUS", "ML", "ADD"),
            (2, "S", "FB", "ADD"),
            (3, "CHN", "ML", "ADD"),
        ],
    )
    current.reader.tables["fxm_geo"][0].update(lat_dec=60.5, long_dec=28.7)
    current.reader.tables["fxm_geo"][1].update(lat_dec=60.5, long_dec=28.7)
    current.reader.tables["fxm_geo"][2].update(lat_dec=60.5, long_dec=28.7)
    notices = TerrestrialIssue(current.reader).notices

    result = filter_near_finland(
        notices,
        neighbour_adms=["RUS", "S"],
        distance_limits_km={"150-151": 100, "default": 1},
    )
    assert [item.notice.terrakey for item in result] == ["1", "2"]
    assert all(item.distance_to_finland_km <= item.distance_limit_km for item in result)


def test_filter_excludes_missing_coordinates_and_distant_stations():
    current = issue(3076, [(1, "RUS", "ML", "ADD"), (2, "RUS", "ML", "ADD")])
    current.reader.tables["fxm_geo"][0].update(lat_dec=None)
    current.reader.tables["fxm_geo"][1].update(lat_dec=75.0, long_dec=60.0)
    notices = TerrestrialIssue(current.reader).notices

    assert filter_near_finland(notices, neighbour_adms=["RUS"], distance_limits_km={"default": 10}) == []
    assert distance_to_finland(None, 28.7) is None
    assert distance_to_finland(60.5, 28.7).precision.startswith("approximate")


def test_article_11_totals_are_available_for_reconciliation():
    current = issue(3076, [(1, "RUS", "ML", "ADD")])
    current.reader.tables["pub"][0]["pub_prefix"] = "NTFD_RR"
    current.reader.tables["pub_totals"] = [
        {"pub_no": 1, "pub_prefix": "P", "addendum": 0, "corrigendum": 0, "pub_part": "1", "no_publish": 99},
        {"pub_no": 1, "pub_prefix": "NTFD_RR", "addendum": 0, "corrigendum": 0, "pub_part": "1", "no_publish": 12},
        {"pub_no": 1, "pub_prefix": "NTFD_RR", "addendum": 0, "corrigendum": 0, "pub_part": "2", "no_publish": 3},
    ]
    parsed = TerrestrialIssue(current.reader)
    assert parsed.article11_totals == {"1": 12, "2": 3}
    assert parsed.published_assignment_count == 15
    assert parsed.article11_reconciliation == {
        "article11_total": 15,
        "fxm_terra_rows": 1,
        "fxm_notice_rows": 0,
        "fxm_recorded_rows": 1,
        "publication_history_rows": 0,
        "unreconciled_article11_minus_notice_rows": 15,
    }
