import pytest

from ific_guard.terrestrial.adapter import TerrestrialIssue
from ific_guard.terrestrial.diff import diff_issues
from tests.test_screening import FakeReader


def reader(issue, assignments, published=None, targets=()):
    """Assignments are (terrakey, adm, class, intent). All are published unless `published` narrows them."""
    keys = [a[0] for a in assignments] if published is None else published
    return FakeReader(
        pub_current_circ=[dict(circ_no=issue, d_pub="2026-07-21 00:00:00")],
        ref_stn_cls=[dict(stn_cls="FB", description="Base station"), dict(stn_cls="ML", description="Land mobile station")],
        ref_nat_srv=[dict(nat_srv="CV", description="Correspondence")],
        pub=[dict(pub_no=1, circ_no=issue, pub_prefix="P", addendum=0, corrigendum=0)],
        fxm_pub_hist=[dict(terrakey=k, pub_no=1, pub_prefix="P", addendum=0, corrigendum=0) for k in keys],
        pub_totals=[],
        fxm_terra=[
            dict(terrakey=k, adm=adm, stn_cls=cls, intent=intent, freq_assgn=150.0, freq_min=149.9, freq_max=150.1,
                 bdwdth=25.0, geo_key=f"g{k}")
            for k, adm, cls, intent in assignments
        ],
        fxm_geo=[dict(geo_key=f"g{a[0]}", lat_dec=60.5, long_dec=28.7, geo_type="POINT", radius=None) for a in assignments],
        fxm_ant=[],
        fxm_ant_hgt=[],
        fxm_nat_serv=[dict(terrakey=a[0], nat_srv="CV") for a in assignments],
        fxm_target=[dict(terrakey=k, target=t, action=action) for k, t, action in targets],
        fxm_coord=[],
    )


def issue(*args, **kwargs):
    return TerrestrialIssue(reader(*args, **kwargs))


def test_only_land_mobile_notices_published_in_this_issue_are_emitted():
    current = issue(
        3076,
        [(1, "RUS", "ML", "ADD"), (2, "RUS", "FB", "ADD"), (3, "RUS", "FX", "ADD"), (4, "RUS", "ML", "ADD")],
        published=[1, 2, 3],  # 4 is an older recorded assignment, kept only as a target for changes
    )
    assert set(current.notices) == {"1", "2"}
    assert "4" in current.all_assignments
    notice = current.notices["1"]
    assert (notice.low_mhz, notice.high_mhz, notice.latitude_deg, notice.nature_services) == (149.9, 150.1, 60.5, ["CV"])


def test_a_database_for_a_different_issue_is_refused():
    with pytest.raises(ValueError, match="Expected terrestrial issue 3075"):
        TerrestrialIssue(reader(3076, []), expected_issue=3075)


def test_bad_coordinates_are_flagged_and_the_notice_is_kept():
    r = reader(3076, [(1, "RUS", "ML", "ADD")])
    r.tables["fxm_geo"][0].update(lat_dec=None)
    notice = TerrestrialIssue(r).notices["1"]
    assert notice.latitude_deg is None and "coordinates" in notice.quality_flags[0]


def test_a_missing_interval_is_derived_from_centre_and_bandwidth_and_flagged():
    r = reader(3076, [(1, "RUS", "ML", "ADD")])
    r.tables["fxm_terra"][0].update(freq_min=None, freq_max=None)
    notice = TerrestrialIssue(r).notices["1"]
    assert (round(notice.low_mhz, 4), round(notice.high_mhz, 4)) == (149.9875, 150.0125)
    assert "derived" in notice.quality_flags[0]


def test_diff_classifies_from_the_notified_intent_not_from_absence():
    previous = issue(3075, [(9, "RUS", "ML", "ADD"), (8, "RUS", "ML", "ADD")])
    current = issue(
        3076,
        [(1, "RUS", "ML", "ADD"), (2, "RUS", "ML", "MODIFY"), (3, "EST", "FB", "SUPPRESS")],
        targets=[(2, 9, "MODIFY"), (3, 8, "SUPPRESS")],
    )
    changes = {c.terrakey: c for c in diff_issues(previous, current)}
    assert {k: c.kind for k, c in changes.items()} == {"1": "new", "2": "modified", "3": "withdrawn"}
    assert changes["2"].target_ids == ("9",) and changes["2"].target_in_previous
    # 8 and 9 are absent from the current notices, yet that alone produces no "withdrawn"
    assert "9" not in changes and "8" not in changes


def test_conflicting_intent_and_target_action_goes_to_expert_review():
    previous = issue(3075, [(9, "RUS", "ML", "ADD")])
    current = issue(3076, [(1, "RUS", "ML", "ADD")], targets=[(1, 9, "SUPPRESS")])
    change = diff_issues(previous, current)[0]
    assert change.kind == "unknown" and "expert review" in change.basis


def test_diff_needs_consecutive_issues():
    with pytest.raises(ValueError, match="consecutive"):
        diff_issues(issue(3074, []), issue(3076, []))
