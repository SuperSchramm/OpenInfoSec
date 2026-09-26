"""Issue #51: the other paths to a decision proposal (briefing alert, alert ack,
activity feed, chat tools) follow the same rule as /decisions (issue #46):
the principal and the person it was routed to, nobody else."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.alerts import store as alert_store
from openexecutive.api.routes import alerts as alerts_route
from openexecutive.api.routes import today as today_route
from openexecutive.briefing import narrative_cache
from openexecutive.departments import registry as dept_registry
from openexecutive.departments import store as dept_store
from openexecutive.memory import decision_ledger, episodic
from openexecutive.orchestrator.calendar_tools import handle_cancel_calendar_event
from openexecutive.orchestrator.schedule_tools import handle_ack_alert
from openexecutive.orchestrator.turn_identity import TurnCaller, current_turn_caller
from openexecutive.people import insights_cache
from openexecutive.people import registry as people_registry
from openexecutive.people import store as people_store
from openexecutive.workflows import persistence as wf_persistence

ALEX = {"x-caller-email": "alex@example.com"}  # principal
SABIN = {"x-caller-email": "sabin@example.com"}  # rostered teammate, the approver
RIO = {"x-caller-email": "rio@example.com"}  # rostered teammate, not the approver
STRANGER = {"x-caller-email": "stranger@example.com"}  # signed in, not rostered


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the briefing's background insight/narrative generation off the network."""
    from openexecutive.people import insights as insights_mod

    async def _none(*_a: object, **_k: object) -> None:
        return None

    async def _noop(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(insights_mod, "generate_person_insight", _none)
    monkeypatch.setattr(today_route, "_regen_briefing_narrative", _noop)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    db = tmp_path / "w.db"
    for mod in (episodic, dept_store, people_store, alert_store, wf_persistence,
                insights_cache, narrative_cache):
        monkeypatch.setattr(mod, "DB_PATH", db)
    dept_registry.invalidate()
    people_registry.invalidate()
    episodic.initialize_db(db)
    dept_store.initialize_db(db)
    people_store.initialize_db(db)
    alert_store.initialize_db(db)
    wf_persistence.initialize_runs_db(db)
    insights_cache.initialize_db(db)
    narrative_cache.initialize_db(db)
    ids = {
        "alex": people_store.upsert_person(full_name="Alex", is_principal=True, email="alex@example.com"),
        "sabin": people_store.upsert_person(full_name="Sabin", email="sabin@example.com"),
        "rio": people_store.upsert_person(full_name="Rio", email="rio@example.com"),
    }
    people_registry.invalidate()
    return ids


@pytest.fixture()
def client(world: dict[str, int]) -> TestClient:
    app = FastAPI()
    app.include_router(today_route.router)
    app.include_router(alerts_route.router)
    return TestClient(app)


def _decision(approver: int | None, key: str, *, resolve: bool = False, title: str = "Secret sync") -> tuple[int, int]:
    """A decision plus its companion alert; returns (instance_id, alert_id)."""
    iid = decision_ledger.create_decision_instance(
        decision_class="meeting_scheduling", department="operations",
        originating_session_id=None,
        proposed_payload={"title": title, "summary": title},
        idempotency_key=key, gate_mode="propose",
        approver_person_id=approver, confidence=0.9,
    )
    aid = alert_store.insert_alert(
        source=decision_ledger.DECISION_ALERT_SOURCE, external_id=f"decision:{iid}",
        severity="medium", headline=f"Approve meeting: {title}", body=f"Meeting: {title}",
        suggested_action="Book it.",
        topic_tags=[decision_ledger.decision_instance_tag(iid), "decision_class:meeting_scheduling"],
        dedup_key=f"decision:{iid}", routed_to_person_id=approver,
    )
    if resolve:
        decision_ledger.mark_resolved(iid, decision_ledger.STATUS_APPROVED_UNCHANGED)
    return iid, aid


def _titles(client: TestClient, headers: dict[str, str] | None = None) -> set[str]:
    return {p["headline"] for p in client.get("/today", headers=headers or {}).json()["proposals"]}


# --- /today and /morning-brief ------------------------------------------------

def test_today_shows_a_decision_alert_only_to_its_owner(client: TestClient, world: dict[str, int]) -> None:
    _decision(world["sabin"], "k1", title="For Sabin")
    _decision(None, "k2", title="For Alex")
    assert _titles(client, ALEX) == {"Approve meeting: For Sabin", "Approve meeting: For Alex"}
    assert _titles(client) == _titles(client, ALEX)  # no header: the principal
    assert _titles(client, SABIN) == {"Approve meeting: For Sabin"}
    assert _titles(client, RIO) == set()
    assert _titles(client, STRANGER) == set()


def test_today_leaves_ordinary_alerts_alone(client: TestClient) -> None:
    alert_store.insert_alert(source="triage", external_id="t1", severity="low", headline="Newsletter", body="x")
    for h in (ALEX, SABIN, RIO, STRANGER):
        assert "Newsletter" in _titles(client, h)


def test_morning_brief_alias_is_scoped_too(client: TestClient, world: dict[str, int]) -> None:
    _decision(None, "k3", title="For Alex")
    body = client.get("/morning-brief", headers=RIO).json()["proposals"]
    assert body == []


def test_unrostered_caller_gets_no_company_wide_narrative(
    client: TestClient, world: dict[str, int]
) -> None:
    from openexecutive.briefing import narrative_cache as nc

    nc.put(nc.BriefingNarrative(
        scope=nc.DEFAULT_SCOPE, input_hash="h", generated_at="2026-01-01T00:00:00+00:00",
        narrative_text="Alex is booking a Secret sync with the board",
    ))
    assert "Secret sync" in (client.get("/today", headers=ALEX).json().get("narrative") or "")
    assert "Secret sync" not in (client.get("/today", headers=STRANGER).json().get("narrative") or "")


# --- /today/activity ----------------------------------------------------------

def test_activity_hides_other_peoples_resolved_decisions(client: TestClient, world: dict[str, int]) -> None:
    _decision(world["sabin"], "a1", resolve=True, title="Sabin thing")
    _decision(None, "a2", resolve=True, title="Alex thing")

    def summaries(h: dict[str, str]) -> str:
        return " ".join(i["summary"] for i in client.get("/today/activity", headers=h).json()["items"])

    assert "Sabin thing" in summaries(ALEX) and "Alex thing" in summaries(ALEX)
    assert "Sabin thing" in summaries(SABIN) and "Alex thing" not in summaries(SABIN)
    assert "thing" not in summaries(RIO)
    assert "thing" not in summaries(STRANGER)


# --- POST /alerts/{id}/ack ----------------------------------------------------

def test_ack_route_is_owner_only_for_decision_alerts(client: TestClient, world: dict[str, int]) -> None:
    _, aid = _decision(world["sabin"], "b1")
    for h in (RIO, STRANGER):
        assert client.post(f"/alerts/{aid}/ack", json={"status": "dismissed"}, headers=h).status_code == 404
    assert alert_store.get_alert(aid).status == "unread"
    assert client.post(f"/alerts/{aid}/ack", json={"status": "dismissed"}, headers=SABIN).status_code == 200


def test_ack_route_principal_can_clear_and_unknown_id_matches_forbidden(
    client: TestClient, world: dict[str, int]
) -> None:
    _, aid = _decision(world["sabin"], "b2")
    forbidden = client.post(f"/alerts/{aid}/ack", json={"status": "read"}, headers=RIO)
    unknown = client.post("/alerts/99999/ack", json={"status": "read"}, headers=RIO)
    assert (forbidden.status_code, forbidden.json()) == (unknown.status_code, unknown.json())
    assert client.post(f"/alerts/{aid}/ack", json={"status": "read"}, headers=ALEX).status_code == 200


def test_ack_route_ordinary_alert_unchanged(client: TestClient) -> None:
    aid = alert_store.insert_alert(source="triage", external_id="t2", severity="low", headline="h", body="b")
    assert client.post(f"/alerts/{aid}/ack", json={"status": "read"}, headers=RIO).status_code == 200


# --- chat tools -----------------------------------------------------------------

def _as(person_id: int | None, *, web: bool = True):
    return current_turn_caller.set(TurnCaller(person_id=person_id, from_web_chat=web))


def _run(coro) -> dict:
    return json.loads(asyncio.run(coro))


def test_ack_alert_tool_refuses_a_teammate_a_stranger_and_unverified_surfaces(world: dict[str, int]) -> None:
    _, aid = _decision(world["sabin"], "c1")
    for who, web in ((world["rio"], True), (None, True), (world["sabin"], False), (world["alex"], False)):
        tok = _as(who, web=web)
        try:
            assert _run(handle_ack_alert({"alert_id": aid, "status": "dismissed"}))["status"] == "refused"
        finally:
            current_turn_caller.reset(tok)
    assert alert_store.get_alert(aid).status == "unread"
    # no recorded caller at all (scheduler, workflow, CLI) fails closed too
    assert _run(handle_ack_alert({"alert_id": aid, "status": "dismissed"}))["status"] == "refused"


def test_ack_alert_tool_allows_owner_and_principal_and_leaves_ordinary_alerts(world: dict[str, int]) -> None:
    _, aid = _decision(world["sabin"], "c2")
    tok = _as(world["sabin"])
    try:
        assert _run(handle_ack_alert({"alert_id": aid, "status": "dismissed"}))["status"] == "dismissed"
    finally:
        current_turn_caller.reset(tok)
    _, aid2 = _decision(world["sabin"], "c3")
    tok = _as(world["alex"])
    try:
        assert _run(handle_ack_alert({"alert_id": aid2, "status": "ack"}))["status"] == "ack"
    finally:
        current_turn_caller.reset(tok)
    plain = alert_store.insert_alert(source="triage", external_id="t3", severity="low", headline="h", body="b")
    assert _run(handle_ack_alert({"alert_id": plain, "status": "ack"}))["status"] == "ack"  # no caller needed


def test_cancel_tool_is_owner_only(world: dict[str, int]) -> None:
    iid, _ = _decision(world["sabin"], "d1")
    for who, web in ((world["rio"], True), (None, True), (world["sabin"], False)):
        tok = _as(who, web=web)
        try:
            assert _run(handle_cancel_calendar_event({"decision_instance_id": iid}))["status"] == "refused"
        finally:
            current_turn_caller.reset(tok)
    assert _run(handle_cancel_calendar_event({"decision_instance_id": iid}))["status"] == "refused"
    assert decision_ledger.get_decision_instance(iid).status == "proposed"
    tok = _as(world["sabin"])
    try:
        assert _run(handle_cancel_calendar_event({"decision_instance_id": iid}))["status"] == "cancelled"
    finally:
        current_turn_caller.reset(tok)


# --- internal callers, and the chat briefing context ----------------------------

def test_internal_activity_builds_still_include_every_resolved_decision(world: dict[str, int]) -> None:
    """The morning brief, digest and reflection call _build_activity with no viewer
    and must keep seeing every resolved decision; only the HTTP route is scoped."""
    _decision(world["sabin"], "e1", resolve=True, title="Sabin thing")
    _decision(None, "e2", resolve=True, title="Alex thing")
    text = " ".join(i.summary for i in today_route._build_activity(20).items)
    assert "Sabin thing" in text and "Alex thing" in text


def test_chat_briefing_context_leaves_out_other_peoples_decision_alerts(world: dict[str, int]) -> None:
    from openexecutive.briefing.context import format_open_alerts_for_prompt

    _decision(world["sabin"], "f1", title="For Sabin")
    _decision(None, "f2", title="For Alex")
    alert_store.insert_alert(source="triage", external_id="t9", severity="low", headline="Newsletter", body="x")

    def ctx(caller: int | None) -> str:
        return format_open_alerts_for_prompt(scope_to_caller=True, caller_person_id=caller)

    alex, sabin, rio = ctx(world["alex"]), ctx(world["sabin"]), ctx(world["rio"])
    assert "For Sabin" in alex and "For Alex" in alex
    assert "For Sabin" in sabin and "For Alex" not in sabin
    assert "For Sabin" not in rio and "For Alex" not in rio and "Newsletter" in rio
    assert "For Sabin" not in ctx(None) and "Newsletter" in ctx(None)
    assert "For Sabin" in format_open_alerts_for_prompt()  # unscoped default unchanged
