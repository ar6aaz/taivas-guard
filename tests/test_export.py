import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from ific_guard.cli import main
from ific_guard.db import session_scope
from ific_guard.export import export_issue
from ific_guard.models import Artifact, Case, CaseOccurrence, Event
from ific_guard.screening.engine import screen_artifact
from tests.conftest import make_zip
from tests.test_screening import FakeReader, freq, notice


def source(tmp_path, issue=3074, sha="a" * 64):
    path = tmp_path / f"{issue}-{sha[:4]}.mdb"
    path.write_bytes(b"test database")
    with session_scope() as s:
        row = Artifact(domain="space", issue_no=issue, source_url="https://www.itu.int/test.zip",
                       sha256=sha, size_bytes=20, mdb_path=str(path))
        s.add(row)
        s.flush()
        return row.id


@pytest.fixture
def fake_reader(monkeypatch):
    reader = FakeReader(
        notice=[notice(10), notice(20)],
        geo=[dict(ntc_id=10, sat_name="Medium", long_nom=25), dict(ntc_id=20, sat_name="High", long_nom=25)],
        freq=[freq(10, 1410, 1412), freq(20, 1410, 1412)],
        grp=[dict(grp_id=2, ntc_id=20)],
        provn=[dict(grp_id=2, adm="FIN", coord_prov="9.7")],
    )
    monkeypatch.setattr("ific_guard.export.MdbReader", lambda path: reader)
    return reader


def test_export_is_repeatable_ranked_traceable_and_has_no_side_effects(tmp_path, fake_reader, monkeypatch):
    source(tmp_path)
    monkeypatch.setenv("IFIC_API_KEY", "do-not-export-this")
    first, second = tmp_path / "first", tmp_path / "second"
    a = export_issue("space", 3074, first)
    b = export_issue("space", 3074, second)
    for name in ("cases.csv", "cases.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    for key in ("generated_at", "run_time_s"):
        a.pop(key)
        b.pop(key)
    assert a == b
    rows = json.loads((first / "cases.json").read_text(encoding="utf-8"))
    assert rows and all(r["source_sha256"] == "a" * 64 and r["notice_ids"] for r in rows)
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0]["severity"] == "high"
    with (first / "cases.csv").open(encoding="utf-8", newline="") as f:
        assert len(list(csv.DictReader(f))) == len(rows)
    assert "do-not-export-this" not in (first / "manifest.json").read_text()
    with session_scope() as s:
        assert list(s.scalars(select(Case))) == []
        assert list(s.scalars(select(Event))) == []


def test_export_uses_requested_issue_and_revision_not_latest_global_case(tmp_path, fake_reader, monkeypatch):
    source(tmp_path)
    source(tmp_path, sha="b" * 64)
    source(tmp_path, issue=3083, sha="c" * 64)
    paths = []
    monkeypatch.setattr("ific_guard.export.MdbReader", lambda path: paths.append(path) or fake_reader)
    assert export_issue("space", 3074, tmp_path / "latest")["source_sha256"] == "b" * 64
    assert "3074-bbbb" in paths[-1]
    assert export_issue("space", 3074, tmp_path / "old", sha256="a" * 64)["source_sha256"] == "a" * 64
    assert "3074-aaaa" in paths[-1]


def test_missing_or_unsupported_issue_does_not_create_empty_success(tmp_path):
    for domain in ("space", "terrestrial"):
        with pytest.raises(ValueError):
            export_issue(domain, 3074, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_zero_findings_keeps_headers_and_rule_scope(tmp_path, fake_reader):
    source(tmp_path)
    fake_reader.tables.clear()
    fake_reader.tables["srs_ooak"] = [{"version_no": 10}]
    manifest = export_issue("space", 3074, tmp_path / "out")
    assert manifest["case_count"] == 0
    assert manifest["rule_ids"] == ["fin-named-by-br", "m4-no-space-to-earth", "station-rx-overlap", "pfd"]
    assert manifest["scope"]["ap30_coverage"] == "not implemented"
    assert (tmp_path / "out/cases.json").read_text().strip() == "[]"
    assert (tmp_path / "out/cases.csv").read_text().startswith("rank,domain,issue_no,")


@pytest.mark.parametrize("order", [(3074,), (3074, 3083), (3083, 3074)])
def test_issue_findings_and_exports_do_not_depend_on_processing_order(tmp_path, monkeypatch, order):
    readers = {
        3074: FakeReader(notice=[notice(10)], geo=[dict(ntc_id=10, sat_name="Original", long_nom=25)],
                         freq=[freq(10, 1410, 1412)]),
        # Same family, materially different later finding and a second family.
        3083: FakeReader(notice=[notice(10), notice(20)],
                         geo=[dict(ntc_id=i, sat_name="Later", long_nom=25) for i in (10, 20)],
                         freq=[freq(i, 1660.1, 1660.4) for i in (10, 20)]),
    }
    ids = {n: source(tmp_path, n, str(n)[0] * 64) for n in (3074, 3083)}
    monkeypatch.setattr("ific_guard.export.MdbReader", lambda path: readers[int(Path(path).name[:4])])
    baseline = tmp_path / "baseline"
    export_issue("space", 3074, baseline)
    for n in order:
        screen_artifact(ids[n], reader=readers[n])
    export_issue("space", 3074, tmp_path / "after")
    for name in ("cases.csv", "cases.json"):
        assert (baseline / name).read_bytes() == (tmp_path / "after" / name).read_bytes()
    with session_scope() as s:
        occurrence = s.scalars(select(CaseOccurrence).where(CaseOccurrence.artifact_id == ids[3074])).one()
        assert occurrence.hit["network"] == "Original"
        assert occurrence.hit["notice_ids"] == [10]
        assert occurrence.hit["details"]["bands"][0]["low_mhz"] == 1400
        if 3083 in order:
            later = list(s.scalars(select(CaseOccurrence).where(CaseOccurrence.artifact_id == ids[3083])))
            assert len(later) == 2
            assert all(row.hit["network"] == "Later" for row in later)
            assert occurrence.case_id in {row.case_id for row in later}


def test_rescreen_replaces_only_its_own_occurrences(tmp_path, fake_reader):
    first, second = source(tmp_path), source(tmp_path, 3083, "b" * 64)
    screen_artifact(first, reader=fake_reader)
    screen_artifact(second, reader=fake_reader)
    screen_artifact(first, reader=fake_reader)
    with session_scope() as s:
        before = list(s.scalars(select(CaseOccurrence).where(CaseOccurrence.artifact_id == second)))
        assert len(before) == 3
        assert len(list(s.scalars(select(CaseOccurrence)))) == 6
    screen_artifact(first, reader=FakeReader())
    with session_scope() as s:
        remaining = list(s.scalars(select(CaseOccurrence)))
        assert len(remaining) == 3
        assert all(row.artifact_id == second for row in remaining)


def test_manifest_hashes_verify_and_files_use_fixed_line_endings(tmp_path, fake_reader):
    source(tmp_path)
    manifest = export_issue("space", 3074, tmp_path / "out")
    assert manifest["case_count"] > 0
    for name, recorded in manifest["output_sha256"].items():
        content = (tmp_path / "out" / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == recorded
        assert b"\r" not in content  # Windows must not produce files git would rewrite
    assert b"\r" not in (tmp_path / "out" / "manifest.json").read_bytes()


def test_cli_import_does_not_record_a_local_path(tmp_path, monkeypatch):
    archive = tmp_path / "ific3083.zip"
    archive.write_bytes(make_zip())
    monkeypatch.setattr(sys, "argv", ["ific_guard.cli", "import", str(archive)])
    main()
    with session_scope() as s:
        assert s.scalars(select(Artifact)).one().source_url == "import:ific3083.zip"
