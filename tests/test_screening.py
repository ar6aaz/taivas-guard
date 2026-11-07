import struct
from datetime import date

from sqlalchemy import select

from ific_guard import events
from ific_guard.config import get_settings
from ific_guard.db import session_scope
from ific_guard.models import Artifact, Case, Event
from ific_guard.screening.engine import screen_artifact
from ific_guard.screening.rules import FinlandNamedRule, M4Band, M4NoDownlinkRule
from ific_guard.screening.space import SpaceIssue, add_months

BAND = M4Band(3400, 3800, "MOBILE; FIXED", "5G", False, "high", "120-121")
PASSIVE = M4Band(1400, 1427, "EARTH EXPLORATION-SATELLITE (passive)", "", True, "high", "90")


class FakeReader:
    def __init__(self, **tables):
        tables.setdefault("srs_ooak", [{"version_no": 10}])
        self.tables = tables

    def table(self, name):
        return self.tables.get(name, [])


def notice(ntc_id, adm="USA", ntc_type="G", reason="C", act="A", target=None):
    return dict(ntc_id=ntc_id, adm=adm, ntc_type=ntc_type, ntf_rsn=reason, act_code=act, tgt_ntc_id=target,
                prov="9.6", d_wic="2026-10-27 00:00:00")


def freq(ntc_id, low, high, emi="E", grp=1):
    return dict(ntc_id=ntc_id, grp_id=grp, emi_rcp=emi, freq_min=low, freq_max=high)


def m4_hits(**tables):
    return list(M4NoDownlinkRule(get_settings(), [BAND, PASSIVE]).run(SpaceIssue(FakeReader(**tables))))


def test_gso_downlink_in_band_without_allocation_is_flagged():
    hits = m4_hits(notice=[notice(1)], geo=[dict(ntc_id=1, sat_name="SAT", long_nom=25.0)], freq=[freq(1, 3500, 3540)])
    assert len(hits) == 1
    assert hits[0].severity == "medium"
    assert hits[0].details["bands"][0]["m4_table_pages"] == "120-121"
    assert hits[0].comment_deadline == date(2027, 2, 27)


def test_gso_beyond_the_visible_arc_is_not_flagged():
    assert not m4_hits(notice=[notice(1)], geo=[dict(ntc_id=1, long_nom=140.0)], freq=[freq(1, 3500, 3540)])


def test_uplink_touching_band_and_out_of_scope_notices_are_not_flagged():
    geo = [dict(ntc_id=i, long_nom=25.0) for i in (1, 2, 3, 4)]
    assert not m4_hits(
        notice=[notice(1), notice(2), notice(3, reason="N"), notice(4, act="S")],
        geo=geo,
        freq=[freq(1, 3500, 3540, emi="R"), freq(2, 3800, 3840), freq(3, 3500, 3540), freq(4, 3500, 3540)],
    )


def test_ngso_uses_latitude_reach_and_keeps_missing_inclination():
    tables = dict(notice=[notice(1, ntc_type="N"), notice(2, ntc_type="N"), notice(3, ntc_type="N")],
                  freq=[freq(i, 3500, 3540) for i in (1, 2, 3)],
                  orbit=[dict(ntc_id=1, inclin_ang=98.0), dict(ntc_id=2, inclin_ang=30.0)])
    assert sorted(h.family_id for h in m4_hits(**tables)) == [1, 3]  # 98 deg reaches 82 deg; 3 has no data


def test_passive_band_raises_severity():
    hits = m4_hits(notice=[notice(1)], geo=[dict(ntc_id=1, long_nom=25.0)], freq=[freq(1, 1410, 1412)])
    assert hits[0].severity == "high"


def test_a_modification_and_its_target_form_one_case():
    hits = m4_hits(
        notice=[notice(1), notice(9, act="M", target=1)],
        geo=[dict(ntc_id=1, long_nom=25.0), dict(ntc_id=9, long_nom=25.0)],
        freq=[freq(1, 3500, 3540), freq(9, 3500, 3540)],
    )
    assert len(hits) == 1 and hits[0].notice_ids == [1, 9]


def test_finland_named_by_br_is_found_in_both_tables():
    reader = FakeReader(
        notice=[notice(1), notice(2), notice(3)],
        grp=[dict(grp_id=10, ntc_id=1), dict(grp_id=30, ntc_id=3)],
        provn=[dict(grp_id=10, adm="FIN", ctry=None, coord_prov="9.7"), dict(grp_id=30, adm="SWE", ctry=None, coord_prov="9.7")],
        tr_provn=[dict(ntc_id=2, adm=None, ctry="FIN", coord_prov="9.14")],
    )
    hits = list(FinlandNamedRule(get_settings()).run(SpaceIssue(reader)))
    assert {h.family_id: h.details["provisions"] for h in hits} == {1: ["9.7"], 2: ["9.14"]}


def test_comment_deadline_clamps_to_month_end():
    assert add_months(date(2026, 10, 31), 4) == date(2027, 2, 28)


def _artifact(domain="space", issue=3083, sha="a" * 64):
    with session_scope() as s:
        row = Artifact(domain=domain, issue_no=issue, source_url="u", sha256=sha, size_bytes=1, mdb_path="x")
        s.add(row)
        s.flush()
        return row.id


def _tables(low=3500):
    return FakeReader(notice=[notice(1)], geo=[dict(ntc_id=1, sat_name="SAT", long_nom=25.0)], freq=[freq(1, low, low + 40)])


def _rules():
    return [M4NoDownlinkRule(get_settings(), [BAND, PASSIVE])]


def test_rescreening_is_idempotent_and_only_material_change_reopens_a_case():
    first = _artifact()
    assert screen_artifact(first, _tables(), _rules())["opened"] == 1
    assert screen_artifact(first, _tables(), _rules()) == {"artifact_id": first, "status": "done", "opened": 0, "changed": 0}

    with session_scope() as s:
        s.scalars(select(Case)).one().review_status = "rejected"
    later = _artifact(issue=3084, sha="b" * 64)
    screen_artifact(later, _tables(), _rules())  # same content in a later issue
    with session_scope() as s:
        case = s.scalars(select(Case)).one()
        assert (case.last_issue, case.review_status, case.changed_artifact_id) == (3084, "rejected", first)

    newer = _artifact(issue=3085, sha="c" * 64)
    assert screen_artifact(newer, _tables(1410), _rules())["changed"] == 1
    with session_scope() as s:
        case = s.scalars(select(Case)).one()
        assert (case.review_status, case.severity, case.changed_artifact_id) == ("pending", "high", newer)


def test_an_older_issue_processed_later_moves_first_issue_back_without_reopening():
    newer = _artifact(issue=3083, sha="a" * 64)
    screen_artifact(newer, _tables(), _rules())
    older = _artifact(issue=3074, sha="b" * 64)
    assert screen_artifact(older, _tables(1410), _rules())["changed"] == 0
    with session_scope() as s:
        case = s.scalars(select(Case)).one()
        assert (case.first_issue, case.last_issue, case.changed_artifact_id) == (3074, 3083, newer)


def test_terrestrial_issue_with_no_filtered_notices_reports_done_and_publishes_the_event(monkeypatch):
    monkeypatch.setattr("ific_guard.terrestrial.rule_field_strength.load_stations", lambda: [])
    artifact_id = _artifact(domain="terrestrial", issue=3076)
    empty = FakeReader(
        pub_current_circ=[dict(circ_no=3076, d_pub="2026-07-21")],
        ref_stn_cls=[dict(stn_cls="FB", description="Base station"), dict(stn_cls="ML", description="Land mobile station")],
        pub=[dict(pub_no=1, circ_no=3076, pub_prefix="P", addendum=0, corrigendum=0)],
    )
    assert screen_artifact(artifact_id, empty)["status"] == "done"
    with session_scope() as s:
        assert s.get_one(Artifact, artifact_id).screen_status == "done"
        assert s.scalars(select(Event)).one().topic == events.ISSUE_SCREENED


def test_impossible_date_reads_as_null_and_a_good_one_still_parses():
    from access_parser import utils

    from ific_guard.screening.space import _tolerate_invalid_dates

    _tolerate_invalid_dates()
    assert utils.mdb_date_to_readable(9223091111617103207) is None  # the NaN cell found in Space 3077
    good = struct.unpack("<q", struct.pack("<d", 45000.5))[0]
    assert utils.mdb_date_to_readable(good) is not None


def test_terrestrial_masks_follow_the_directory_the_image_sets():
    from ific_guard.engines import pfd
    from ific_guard.terrestrial import rule_field_strength as rule

    assert rule.MASK_DIR is pfd.MASK_DIR

