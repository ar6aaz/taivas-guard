from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ific_guard import events
from ific_guard.config import get_settings
from ific_guard.db import session_scope
from ific_guard.models import Artifact, Event, SyncRun
from ific_guard.sync.app import app as sync_app
from ific_guard.sync.fetch import BadArtifact, download, parse_issue_filename, store_zip
from ific_guard.sync.runner import has_run_before, run_sync, scheduled_pass, seconds_until_next_run
from ific_guard.sync.sources import (
    ListingChanged,
    discover_space,
    parse_space_listing,
    parse_terrestrial_listing,
)

from .conftest import make_zip

# Markup copied from the live ITU pages on 2026-11-07
SPACE_PAGE = """<tr>
    <td align="center"><b>27.10.2026</b></td>
        <td align="center"><b><a href="../ific10/ific3083.zip">3083</a></b></td>
    <td align="center"><a href="//www.itu.int/net/x?sel_ific=3083&amp;ie=y">View</a></td></tr>
<tr>
    <td align="center"><b>13.10.2026</b></td>
        <td align="center"><b><a href="../ific10/ific3082.zip">3082</a></b></td>
</tr>"""
TERR_PAGE = """<tr class="ms-itmhover"><td class="ms-vb2">3083</td><td class="ms-vb2"><nobr>27/10/2026</nobr></td>
<td class="ms-vb2"><a href="/en/ITU-R/terrestrial/brific/BRIFIC/MDB/brific3083.zip">IFIC3083</a></td></tr>
<tr class="ms-alternatingstrong"><td class="ms-vb2">3082</td><td class="ms-vb2"><nobr>13/10/2026</nobr></td>
<td class="ms-vb2"><a href="/en/ITU-R/terrestrial/brific/BRIFIC/MDB/brific3082.zip">IFIC3082</a></td></tr>"""


def test_parse_space_listing_resolves_urls_and_dates():
    refs = parse_space_listing(SPACE_PAGE, "https://www.itu.int/sns/wic/demowic26.html")
    assert refs[0].issue_no == 3083
    assert refs[0].url == "https://www.itu.int/sns/ific10/ific3083.zip"
    assert refs[0].published_on == date(2026, 10, 27)


def test_parse_terrestrial_listing_pairs_each_issue_with_its_date():
    refs = parse_terrestrial_listing(TERR_PAGE, "https://www.itu.int/en/ITU-R/terrestrial/brific/Pages/default.aspx")
    assert [(r.issue_no, r.published_on) for r in refs] == [(3083, date(2026, 10, 27)), (3082, date(2026, 10, 13))]
    assert refs[0].url == "https://www.itu.int/en/ITU-R/terrestrial/brific/BRIFIC/MDB/brific3083.zip"


def test_a_changed_listing_raises_rather_than_looking_like_no_new_issues():
    with pytest.raises(ListingChanged):
        parse_space_listing("<html>Site redesign</html>", "https://www.itu.int/sns/wic/demowic26.html")


def test_a_link_outside_itu_is_refused():
    page = SPACE_PAGE.replace("../ific10/ific3083.zip", "https://evil.example/ific3083.zip")
    with pytest.raises(ListingChanged, match="outside itu.int"):
        parse_space_listing(page, "https://www.itu.int/sns/wic/demowic26.html")


def test_space_discovery_walks_back_a_year_over_a_missing_page():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("demowic27.html"):
            return httpx.Response(404)
        return httpx.Response(200, text=SPACE_PAGE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        refs = discover_space(client, get_settings(), today=date(2027, 1, 2))
    assert {r.issue_no for r in refs} == {3082, 3083}


def _site(zips: dict[str, bytes], etag: str = '"v1"', fail_terrestrial: bool = False):
    calls = {"downloads": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("demowic26.html"):
            return httpx.Response(200, text=SPACE_PAGE)
        if path.endswith("default.aspx"):
            return httpx.Response(503) if fail_terrestrial else httpx.Response(200, text=TERR_PAGE)
        name = path.rsplit("/", 1)[-1]
        if name in zips:
            if request.method == "GET":
                calls["downloads"] += 1
            return httpx.Response(200, content=zips[name], headers={"etag": etag})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


ALL_ZIPS = {
    "ific3083.zip": make_zip("ific3083.mdb"),
    "ific3082.zip": make_zip("ific3082.mdb"),
    "brific3083.zip": make_zip("brific3083.mdb"),
    "brific3082.zip": make_zip("brific3082.mdb"),
}


def test_sync_fetches_both_domains_once_and_announces_each_issue():
    client, calls = _site(ALL_ZIPS)
    run_sync("manual", client)
    assert calls["downloads"] == 4
    run_sync("manual", client)  # nothing new
    assert calls["downloads"] == 4
    with session_scope() as s:
        artifacts = list(s.scalars(select(Artifact)))
        topics = [e.topic for e in s.scalars(select(Event))]
        runs = list(s.scalars(select(SyncRun)))
    assert {(a.domain, a.issue_no) for a in artifacts} == {
        ("space", 3083), ("space", 3082), ("terrestrial", 3083), ("terrestrial", 3082),
    }
    assert topics == [events.ISSUE_FETCHED] * 4
    assert [r.status for r in runs] == ["ok", "ok"]


def test_republished_issue_becomes_a_new_revision_and_keeps_the_old_one():
    client, _ = _site(ALL_ZIPS)
    run_sync("manual", client)
    corrected = dict(ALL_ZIPS, **{"ific3083.zip": make_zip("ific3083.mdb", b"\x00\x01\x00\x00Standard Jet DB\x00corrected")})
    client, _ = _site(corrected, etag='"v2"')
    run_sync("manual", client)
    with session_scope() as s:
        revisions = list(s.scalars(select(Artifact).where(Artifact.domain == "space", Artifact.issue_no == 3083)))
    assert len(revisions) == 2
    assert len({r.mdb_path for r in revisions}) == 2


def test_one_source_down_still_syncs_the_other_and_alerts_ops():
    client, _ = _site(ALL_ZIPS, fail_terrestrial=True)
    run_sync("manual", client)
    with session_scope() as s:
        run = s.scalars(select(SyncRun)).one()
        domains = {a.domain for a in s.scalars(select(Artifact))}
        alerts = list(s.scalars(select(Event).where(Event.topic == events.OPS_ALERT)))
    assert run.status == "partial"
    assert domains == {"space"}
    assert len(alerts) == 1 and "terrestrial" in alerts[0].payload["body"]


def test_a_clean_run_waits_for_the_next_cron_slot_and_a_failed_one_retries_sooner(monkeypatch):
    now = datetime(2026, 11, 7, 0, 30)  # default schedule: next slot is 06:00
    for client, expected in [
        (_site(ALL_ZIPS, fail_terrestrial=True)[0], get_settings().retry_interval_s),
        (_site(ALL_ZIPS)[0], 5.5 * 3600),
    ]:
        monkeypatch.setattr("ific_guard.sync.runner.httpx.Client", lambda *a, c=client, **k: c)
        assert scheduled_pass(now) == expected


def test_cron_schedule_is_configurable_and_a_bad_one_stops_startup(monkeypatch):
    monkeypatch.setenv("IFIC_SYNC_CRON", "30 7 * * 2")  # Tuesdays 07:30, when ITU publishes
    get_settings.cache_clear()
    assert seconds_until_next_run(datetime(2026, 11, 10, 7, 0)) == 30 * 60
    monkeypatch.setenv("IFIC_SYNC_CRON", "every six hours")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="cron"):
        get_settings()


def test_an_installed_system_syncs_on_schedule_but_a_new_one_syncs_at_once():
    assert has_run_before() is False
    run_sync("manual", _site(ALL_ZIPS)[0])
    assert has_run_before() is True


@pytest.mark.parametrize(
    "name, expected",
    [
        ("ific3074.zip", ("space", 3074)),
        ("IFIC3074.MDB", ("space", 3074)),
        ("brific3076.mdb", ("terrestrial", 3076)),
        ("brific3076.zip", ("terrestrial", 3076)),
        ("notes.zip", None),
    ],
)
def test_itu_file_names_identify_the_issue(name, expected):
    assert parse_issue_filename(name) == expected


MDB = b"\x00\x01\x00\x00Standard Jet DB\x00" + b"x" * 64
KEY = {"X-API-Key": "test-key"}


def _upload(client, name, content, **form):
    return client.post("/issues/upload", files={"file": (name, content)}, data=form, headers=KEY)


def test_upload_accepts_a_bare_mdb_and_a_zip_and_announces_each():
    client = TestClient(sync_app)
    bare = _upload(client, "brific3076.mdb", MDB)
    zipped = _upload(client, "ific3074.zip", make_zip("ific3074.mdb"))
    assert (bare.status_code, zipped.status_code) == (201, 201)
    assert (bare.json()["artifact"]["domain"], bare.json()["artifact"]["issue_no"]) == ("terrestrial", 3076)
    assert (zipped.json()["artifact"]["domain"], zipped.json()["artifact"]["issue_no"]) == ("space", 3074)
    assert bare.json()["artifact"]["source_url"] == "upload:brific3076.mdb"
    with session_scope() as s:
        stored = {a.issue_no: a.mdb_path for a in s.scalars(select(Artifact))}
        topics = [e.topic for e in s.scalars(select(Event))]
    assert topics == [events.ISSUE_FETCHED] * 2
    assert all(Path(p).exists() and Path(p).suffix == ".mdb" for p in stored.values())


def test_uploading_the_same_file_twice_is_not_screened_twice():
    client = TestClient(sync_app)
    assert _upload(client, "ific3074.mdb", MDB).json()["new"] is True
    again = _upload(client, "ific3074.mdb", MDB)
    assert (again.status_code, again.json()["new"]) == (200, False)
    with session_scope() as s:
        assert len(list(s.scalars(select(Event)))) == 1


def test_upload_uses_explicit_domain_and_issue_when_the_name_is_unhelpful():
    client = TestClient(sync_app)
    assert _upload(client, "from-colleague.mdb", MDB).status_code == 422
    named = _upload(client, "from-colleague.mdb", MDB, domain="space", issue_no="3050")
    assert named.status_code == 201 and named.json()["artifact"]["issue_no"] == 3050


@pytest.mark.parametrize(
    "name, content, code",
    [
        ("ific3074.exe", MDB, 415),
        ("ific3074.mdb", b"this is not an access database at all, only some text", 400),
        ("ific3074.zip", b"not a zip", 400),
        ("ific3074.zip", make_zip("notes.txt"), 400),
    ],
)
def test_upload_rejects_bad_files_and_stores_nothing(name, content, code):
    assert _upload(TestClient(sync_app), name, content).status_code == code
    with session_scope() as s:
        assert s.scalars(select(Artifact)).first() is None


def test_upload_needs_the_api_key():
    response = TestClient(sync_app).post("/issues/upload", files={"file": ("ific3074.mdb", MDB)})
    assert response.status_code == 401


def test_upload_ignores_a_path_in_the_file_name():
    response = _upload(TestClient(sync_app), "../../ific3074.mdb", MDB)
    assert response.status_code == 201
    with session_scope() as s:
        path = Path(s.scalars(select(Artifact)).one().mdb_path)
    assert path.name == "ific3074.mdb"
    assert path.is_relative_to(get_settings().data_dir / "ific" / "space" / "3074")


def test_an_uploaded_issue_is_not_downloaded_again():
    _upload(TestClient(sync_app), "ific3083.mdb", MDB)
    client, calls = _site(ALL_ZIPS)
    run_sync("manual", client)
    assert calls["downloads"] == 3  # the other three of the four issues


def test_neighbours_default_to_the_itu_codes_for_the_five_countries():
    assert get_settings().neighbour_adms == ["RUS", "S", "EST", "NOR", "DNK"]


def test_download_asks_for_the_uncompressed_file_so_its_length_can_be_checked():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, content=make_zip())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        download(client, "https://www.itu.int/x/ific1.zip", "space", 1, get_settings())
    assert seen["accept-encoding"] == "identity"


def test_truncated_download_is_rejected_and_leaves_nothing_behind():
    def handler(_request):
        return httpx.Response(200, content=b"PK\x03\x04broken", headers={"content-length": "9"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises((BadArtifact, httpx.HTTPError)):
        download(client, "https://www.itu.int/x/ific1.zip", "space", 1, get_settings())
    assert not (get_settings().data_dir / "ific").exists() or not any((get_settings().data_dir / "ific").rglob("*.mdb"))


@pytest.mark.parametrize(
    "archive, message",
    [
        (make_zip("notes.txt"), "found 0"),
        (make_zip("ific.mdb", b"this is not an access file at all, just some text"), "not an Access database"),
        (b"not a zip", "Not a valid zip"),
    ],
)
def test_bad_archives_are_rejected(tmp_path, archive, message):
    path = tmp_path / "ific.zip"
    path.write_bytes(archive)
    with pytest.raises(BadArtifact, match=message):
        store_zip(path, "space", 1, get_settings())


def test_a_path_inside_the_zip_cannot_escape_the_issue_folder(tmp_path):
    path = tmp_path / "ific.zip"
    path.write_bytes(make_zip("../../evil.mdb"))
    stored = store_zip(path, "space", 1, get_settings())
    assert stored.mdb_path.name == "evil.mdb"
    assert stored.mdb_path.is_relative_to(get_settings().data_dir / "ific" / "space" / "1")
