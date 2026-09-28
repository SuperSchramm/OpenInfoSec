"""Issue #52: surfaces that summarise the whole company's briefing are the
principal's alone -- the MCP endpoint (server-to-server only) and the
morning_brief / end_of_day_digest workflows with their stored runs."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import artifacts as artifacts_route
from openexecutive.api.routes import workflows as workflows_route
from openexecutive.orchestrator.turn_identity import TurnCaller, current_turn_caller
from openexecutive.orchestrator.workflow_run_tools import handle_run_workflow
from openexecutive.people import store as people_store
from openexecutive.workflows import COMPANY_WIDE_WORKFLOWS, WORKFLOW_REGISTRY, persistence

ALEX = {"x-caller-email": "alex@example.com"}  # principal
SABIN = {"x-caller-email": "sabin@example.com"}  # rostered teammate
STRANGER = {"x-caller-email": "stranger@example.com"}  # signed in, not rostered


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "people.db")
    monkeypatch.setattr(persistence, "DB_PATH", tmp_path / "runs.db")
    from openexecutive.alerts import store as alert_store

    monkeypatch.setattr(alert_store, "DB_PATH", tmp_path / "alerts.db")
    people_store.initialize_db()
    persistence.initialize_runs_db()
    return {
        "alex": people_store.upsert_person(full_name="Alex", is_principal=True, email="alex@example.com"),
        "sabin": people_store.upsert_person(full_name="Sabin", email="sabin@example.com"),
    }


@pytest.fixture()
def client(world: dict[str, int]) -> TestClient:
    app = FastAPI()
    app.include_router(workflows_route.router)
    app.include_router(artifacts_route.router)
    return TestClient(app)


def test_the_company_wide_workflows_exist() -> None:
    assert set(WORKFLOW_REGISTRY) >= COMPANY_WIDE_WORKFLOWS
    # each of these builds its input from the whole company's unscoped briefing
    assert {"morning_brief", "end_of_day_digest", "executive_reflection"} <= COMPANY_WIDE_WORKFLOWS


# --- MCP ------------------------------------------------------------------------

def test_mcp_refuses_signed_in_users_but_not_server_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api.main import create_app

    monkeypatch.setenv("BACKEND_SHARED_SECRET", "testsecret")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    client = TestClient(create_app())
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    base = {"accept": "application/json, text/event-stream", "x-api-key": "testsecret"}

    for path in ("/mcp", "/mcp/"):
        for who in (SABIN, ALEX, STRANGER):
            r = client.post(path, json=body, headers={**base, **who}, follow_redirects=False)
            assert r.status_code == 403, (path, who)
    ok = client.post("/mcp", json=body, headers=base, follow_redirects=False)
    assert ok.status_code not in (401, 403)
    # the shared-secret gate stays outermost: no key is a 401, never a 403
    no_key = client.post("/mcp", json=body, headers={**SABIN, "accept": base["accept"]}, follow_redirects=False)
    assert no_key.status_code == 401


# --- HTTP: run, list, read, delete ------------------------------------------------

@pytest.mark.parametrize("name", sorted(COMPANY_WIDE_WORKFLOWS))
def test_only_the_principal_can_start_a_company_wide_workflow(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    for who in (SABIN, STRANGER):
        assert client.post(f"/workflows/{name}/runs", json={}, headers=who).status_code == 403
    # the principal gets past the gate (stubbed lookup answers 404, never 403)
    monkeypatch.setattr(workflows_route, "get_workflow", lambda _n: (_ for _ in ()).throw(KeyError(_n)))
    assert client.post(f"/workflows/{name}/runs", json={}, headers=ALEX).status_code == 404
    assert client.post(f"/workflows/{name}/runs", json={}).status_code == 404  # no header: principal


def test_other_workflows_are_not_gated(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflows_route, "get_workflow", lambda _n: (_ for _ in ()).throw(KeyError(_n)))
    assert client.post("/workflows/board_prep/runs", json={}, headers=SABIN).status_code == 404


def _runs() -> tuple[str, str]:
    persistence.create_run("brief-1", "morning_brief", "Morning brief", {})
    persistence.complete_run("brief-1", artifact="Alex meets the board about the merger")
    persistence.create_run("plan-1", "board_prep", "Board prep", {})
    return "brief-1", "plan-1"


def test_stored_company_wide_runs_are_the_principals_alone(client: TestClient, world: dict[str, int]) -> None:
    brief, other = _runs()
    ids = lambda r: {x["run_id"] for x in r.json()["runs"]}  # noqa: E731
    assert ids(client.get("/workflows/runs", headers=ALEX)) == {brief, other}
    assert ids(client.get("/workflows/runs")) == {brief, other}
    for who in (SABIN, STRANGER):
        assert ids(client.get("/workflows/runs", headers=who)) == {other}
        assert ids(client.get("/workflows/runs", params={"workflow": "morning_brief"}, headers=who)) == set()
        assert client.get(f"/workflows/runs/{brief}", headers=who).status_code == 404
        assert client.delete(f"/workflows/runs/{brief}", headers=who).status_code == 404
        assert client.get(f"/workflows/runs/{other}", headers=who).status_code == 200
    assert persistence.get_run(brief) is not None  # the refused delete did nothing
    assert "merger" in client.get(f"/workflows/runs/{brief}", headers=ALEX).json()["artifact"]


def test_forbidden_and_unknown_run_ids_look_the_same(client: TestClient) -> None:
    brief, _ = _runs()
    a = client.get(f"/workflows/runs/{brief}", headers=SABIN)
    b = client.get("/workflows/runs/nope", headers=SABIN)
    assert a.status_code == b.status_code == 404
    assert a.json().keys() == b.json().keys()


# --- chat tool ---------------------------------------------------------------------

def _as(person_id: int | None, *, web: bool = True):
    return current_turn_caller.set(TurnCaller(person_id=person_id, verified=web))


def _run_tool(inputs) -> dict:
    return json.loads(asyncio.run(handle_run_workflow({"workflow": "morning_brief", "inputs": inputs})))


def test_run_workflow_tool_is_principal_only_for_company_wide_briefs(world: dict[str, int]) -> None:
    for who, web in ((world["sabin"], True), (None, True), (world["alex"], False)):
        tok = _as(who, web=web)
        try:
            assert _run_tool({})["status"] == "refused"
        finally:
            current_turn_caller.reset(tok)
    assert _run_tool({})["status"] == "refused"  # no recorded speaker: scheduler, CLI, adapters
    tok = _as(world["alex"])
    try:
        assert _run_tool("not-an-object").get("status") != "refused"  # past the gate
    finally:
        current_turn_caller.reset(tok)


# --- the same runs through /artifacts, and list paging ------------------------------

def test_company_wide_briefs_are_hidden_from_the_artifacts_gallery(client: TestClient) -> None:
    brief, other = _runs()
    persistence.complete_run(other, artifact="Board prep notes")
    listed = lambda h: {a["id"] for a in client.get("/artifacts", headers=h).json()["artifacts"]}  # noqa: E731
    assert listed(ALEX) == {f"run:{brief}", f"run:{other}"}
    for who in (SABIN, STRANGER):
        assert listed(who) == {f"run:{other}"}
        assert client.get(f"/artifacts/run:{brief}", headers=who).status_code == 404
        assert client.post(f"/artifacts/run:{brief}/archive", headers=who).status_code == 404
        assert client.post(f"/artifacts/run:{brief}/restore", headers=who).status_code == 404
        assert client.delete(f"/artifacts/run:{brief}", headers=who).status_code == 404
        assert client.get(f"/artifacts/run:{other}", headers=who).status_code == 200
    assert persistence.get_run(brief) is not None
    assert "merger" in client.get(f"/artifacts/run:{brief}", headers=ALEX).json()["body"]
    assert client.post(f"/artifacts/run:{brief}/archive", headers=ALEX).status_code == 200


def test_a_teammates_run_list_is_not_starved_by_a_stream_of_briefs(client: TestClient) -> None:
    persistence.create_run("plan-old", "board_prep", "Board prep", {})
    for n in range(6):
        persistence.create_run(f"brief-{n}", "morning_brief", "Morning brief", {})
    got = client.get("/workflows/runs", params={"limit": 3}, headers=SABIN).json()["runs"]
    assert [r["run_id"] for r in got] == ["plan-old"]
    assert len(client.get("/workflows/runs", params={"limit": 3}, headers=ALEX).json()["runs"]) == 3
