from ific_guard.terrestrial.adapter import TerrestrialIssue
from ific_guard.terrestrial.trends import analyse_neighbour_trends, render_neighbour_trends
from tests.test_terrestrial import reader


def issue(number, assignments):
    return TerrestrialIssue(reader(number, assignments))


def test_trends_keep_zero_activity_administrations_and_screen_counts():
    previous = issue(3075, [(1, "RUS", "ML", "ADD"), (2, "S", "FB", "ADD")])
    current = issue(3076, [(3, "RUS", "ML", "ADD")])

    report = analyse_neighbour_trends(
        [previous, current],
        neighbour_adms=["RUS", "S", "EST", "NOR", "DNK"],
        distance_limits_km={"default": 300},
    )

    assert report.activity == {
        "RUS": {3075: 1, 3076: 1},
        "S": {3075: 1, 3076: 0},
        "EST": {3075: 0, 3076: 0},
        "NOR": {3075: 0, 3076: 0},
        "DNK": {3075: 0, 3076: 0},
    }
    assert report.screened_activity["RUS"] == {3075: 1, 3076: 1}
    assert report.screened_activity["EST"] == {3075: 0, 3076: 0}
    assert {observation.key for observation in report.observations} == {
        "administration-activity", "screened-activity", "frequency-band", "station-class", "bandwidth",
    }


def test_trends_use_adapter_filter_and_are_rendered_with_reproduction_commands():
    previous = issue(3075, [(1, "RUS", "ML", "ADD"), (2, "RUS", "FB", "ADD")])
    previous.reader.tables["fxm_geo"][1].update(lat_dec=75.0, long_dec=60.0)
    current = issue(3076, [(3, "RUS", "ML", "ADD")])

    report = analyse_neighbour_trends(
        [previous, current],
        distance_limits_km={"default": 100},
        reproduction_issues=("/tmp/brific3075.mdb", "/tmp/brific3076.mdb"),
    )
    rendered = render_neighbour_trends(report)

    assert report.screened_activity["RUS"] == {3075: 1, 3076: 1}
    assert "/tmp/brific3075.mdb /tmp/brific3076.mdb" in rendered
    assert "default engineering limit 100 km" in rendered
    assert "not a regulatory value" in rendered
    assert "do not fully reconcile" in rendered
    assert "not confirmed against the ITU ReadMe" in rendered
    assert all(not observation.linked_finding_ids for observation in report.observations)
