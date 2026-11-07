from datetime import date

import httpx
import pytest

from ific_guard.config import get_settings
from ific_guard.export import export_issue
from ific_guard.db import session_scope
from ific_guard.models import Artifact
from ific_guard.screening.engine import screen_artifact
from ific_guard.screening.space import DatabaseVersionError, read_database_version
from ific_guard.sync.sources import discover_space


class Reader:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return self.rows if name == "srs_ooak" else []


def test_unsupported_database_version_is_reported():
    with pytest.raises(DatabaseVersionError, match=r"Unsupported space database version '9'.*srs_ooak"):
        read_database_version(Reader([{"version_no": "9"}]))


def test_missing_database_version_is_reported():
    with pytest.raises(DatabaseVersionError, match="srs_ooak.version_no"):
        read_database_version(Reader([]))


def test_current_database_version_is_supported():
    assert read_database_version(Reader([{"version_no": "Version 10"}])) == "10"


def test_space_discovery_reaches_the_requested_old_issue_and_skips_changed_pages(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "min_issue", 2500)

    def handler(request):
        if request.url.path.endswith("demowic26.html"):
            return httpx.Response(200, text=(
                '<b>27.10.2026</b></td><td><b>'
                '<a href="../ific10/ific3083.zip">3083</a>'
            ))
        if request.url.path.endswith("demowic25.html"):
            return httpx.Response(200, text="<html>legacy redesign</html>")
        if request.url.path.endswith("demowic24.html"):
            return httpx.Response(200, text=(
                '<b>01.01.2024</b></td><td><b>'
                '<a href="../ific9/ific2500.zip">2500</a>'
            ))
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        refs = discover_space(client, settings, today=date(2026, 12, 1))

    assert {ref.issue_no for ref in refs} == {2500, 3083}


def test_current_space_listing_failure_is_not_hidden(monkeypatch):
    settings = get_settings()

    def handler(request):
        if request.url.path.endswith("demowic26.html"):
            return httpx.Response(500)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            discover_space(client, settings, today=date(2026, 12, 1))


def test_screening_rejects_unsupported_version_instead_of_zero_cases(tmp_path):
    path = tmp_path / "unsupported.mdb"
    path.write_bytes(b"placeholder")
    with session_scope() as s:
        artifact = Artifact(
            domain="space", issue_no=3000, source_url="test", sha256="a" * 64,
            size_bytes=11, mdb_path=str(path),
        )
        s.add(artifact)
        s.flush()
        artifact_id = artifact.id

    with pytest.raises(DatabaseVersionError, match="Unsupported space database version"):
        screen_artifact(artifact_id, reader=Reader([{"version_no": 9}]))


def test_export_rejects_unsupported_version_instead_of_zero_findings(tmp_path, monkeypatch):
    path = tmp_path / "3000-aaaa.mdb"
    path.write_bytes(b"placeholder")
    with session_scope() as s:
        s.add(Artifact(
            domain="space", issue_no=3000, source_url="test", sha256="a" * 64,
            size_bytes=11, mdb_path=str(path),
        ))
    monkeypatch.setattr("ific_guard.export.MdbReader", lambda _: Reader([{"version_no": 9}]))
    with pytest.raises(DatabaseVersionError, match="Unsupported space database version"):
        export_issue("space", 3000, tmp_path / "out")
