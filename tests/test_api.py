import pytest
from fastapi.testclient import TestClient

from ific_guard.config import get_settings
from ific_guard.db import session_scope
from ific_guard.models import Artifact, Case
from ific_guard.notifier.app import app as notifier_app
from ific_guard.screening.app import app as screening_app
from ific_guard.sync.app import app as sync_app

KEY = {"X-API-Key": "test-key"}


@pytest.mark.parametrize("app", [sync_app, screening_app, notifier_app])
def test_every_service_reports_health_and_readiness(app):
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/ready").json() == {"status": "ready"}


@pytest.mark.parametrize(
    "app, method, path",
    [
        (sync_app, "post", "/sync"),
        (screening_app, "patch", "/cases/1"),
        (screening_app, "post", "/issues/1/screen"),
        (notifier_app, "post", "/notifications/test"),
    ],
)
def test_write_endpoints_need_the_key_and_stay_closed_without_one(app, method, path, monkeypatch):
    client = TestClient(app)
    assert getattr(client, method)(path, json={}, headers={"X-API-Key": "wrong"}).status_code == 401
    monkeypatch.delenv("IFIC_API_KEY")
    get_settings.cache_clear()
    assert getattr(client, method)(path, json={}, headers=KEY).status_code == 403


def test_expert_can_list_read_and_review_a_case():
    with session_scope() as s:
        s.add(Case(fingerprint="1", domain="space", rule_id="r", severity="high", title="T", adm="USA", network="SAT",
                   notice_ids=["1"], details={"bands": []}, material_hash="m", first_issue=3083, last_issue=3083,
                   changed_artifact_id=1, source_sha256="f" * 64))
    client = TestClient(screening_app)
    assert [c["id"] for c in client.get("/cases", params={"severity": "high"}).json()] == [1]
    assert client.get("/cases", params={"severity": "low"}).json() == []
    assert client.get("/cases/1").json()["source_sha256"] == "f" * 64
    assert client.get("/cases/99").status_code == 404
    reviewed = client.patch("/cases/1", json={"review_status": "accepted", "review_note": "Write to USA"}, headers=KEY)
    assert reviewed.json()["review_status"] == "accepted"
    assert client.patch("/cases/1", json={"review_status": "maybe"}, headers=KEY).status_code == 422


def test_status_flags_a_domain_with_no_data_as_stale():
    with session_scope() as s:
        s.add(Artifact(domain="space", issue_no=3083, source_url="u", sha256="a" * 64, size_bytes=1, mdb_path="x"))
    domains = TestClient(sync_app).get("/status").json()["domains"]
    assert domains["terrestrial"]["stale"] is True and domains["terrestrial"]["latest_issue"] is None
