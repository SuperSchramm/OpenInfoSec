"""Issue #50: every HTTP 403 from `require_install_owner` is now audited (the
Executive's roster-tool refusals already were), and GET /onboard/start is
audited for visibility even though it isn't gated."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import onboarding as onboarding_route
from openexecutive.api.routes import people as people_route
from openexecutive.audit import AuditLogger, set_audit_logger
from openexecutive.people import store as people_store

ALEX = {"x-caller-email": "alex@example.com"}  # principal
SABIN = {"x-caller-email": "sabin@example.com"}  # rostered teammate


@pytest.fixture()
def audit(tmp_path: Path) -> AuditLogger:
    a = AuditLogger(tmp_path / "audit.db")
    set_audit_logger(a)
    return a


@pytest.fixture(autouse=True)
def _people(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "people.db")
    people_store.initialize_db()
    people_store.upsert_person(full_name="Alex", is_principal=True, email="alex@example.com")
    people_store.upsert_person(full_name="Sabin", email="sabin@example.com")


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(people_route.router)
    app.include_router(onboarding_route.router)
    return TestClient(app)


def test_a_refused_people_write_is_audited(client: TestClient, audit: AuditLogger) -> None:
    resp = client.post("/people", json={"full_name": "New Hire"}, headers=SABIN)
    assert resp.status_code == 403
    rows = audit.query(event_type="tool_invocation")
    refusals = [r for r in rows if r.details.get("ok") is False]
    assert len(refusals) == 1
    row = refusals[0]
    assert row.details["route"] == "/people"
    assert row.details["action"] == "change the People list"
    assert "not the principal" in row.summary


def test_an_allowed_people_write_is_not_logged_as_a_refusal(
    client: TestClient, audit: AuditLogger
) -> None:
    resp = client.post("/people", json={"full_name": "New Hire"}, headers=ALEX)
    assert resp.status_code == 201
    assert audit.query(event_type="tool_invocation") == []


def test_onboard_start_is_audited_for_visibility_but_not_gated(
    client: TestClient, audit: AuditLogger
) -> None:
    resp = client.get("/onboard/start", headers=SABIN)
    assert resp.status_code == 200  # a fresh install has no principal to gate against
    rows = audit.query(event_type="tool_invocation")
    starts = [r for r in rows if r.details.get("route") == "/onboard/start"]
    assert len(starts) == 1
    assert starts[0].details["ok"] is True
    assert starts[0].details["wizard_session_id"] == resp.json()["session_id"]
