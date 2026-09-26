"""Only the principal may change the People list (upstream 9325113, adapted).

A People row is the web sign-in allow-list, the outbound-email allow-list and
approval routing. Before this, any signed-in teammate could PATCH the owner's
email to their own, create a second principal, or archive the owner, over HTTP
or by asking the Executive to do it in chat.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.agents import overrides as agent_overrides
from openexecutive.api.routes import chat as chat_route
from openexecutive.api.routes import people as people_route
from openexecutive.departments import registry as dept_registry
from openexecutive.departments import store as dept_store
from openexecutive.memory import episodic as episodic_module
from openexecutive.memory import session_store
from openexecutive.memory.company_profile import CompanyProfile
from openexecutive.orchestrator import people_tools
from openexecutive.orchestrator.turn_identity import TurnCaller, current_turn_caller
from openexecutive.people import registry as people_registry
from openexecutive.people import store as people_store

ALEX = {"x-caller-email": "alex@example.com"}  # the principal (owner)
SABIN = {"x-caller-email": "sabin@example.com"}  # rostered teammate
STRANGER = {"x-caller-email": "stranger@example.com"}  # signed in, not on the roster


@pytest.fixture(autouse=True)
def _never_touch_real_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Some tests here fire requests at fixture-reset / client-swap routes. They must
    be refused (403) before anything runs, but if a gate were ever broken they would
    really run, and everything those routes wipe hangs off two settings: the company
    folder (profile, docs, client slots, user backup) and the vector store. Point
    both at a temp dir for every test in this module, so a broken gate can fail a
    test but never damage a developer's real data."""
    monkeypatch.setenv("COMPANY_PROFILE_PATH", str(tmp_path / "company" / "profile.yaml"))
    monkeypatch.setenv("VECTOR_STORE_PATH", str(tmp_path / "chroma"))


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "episodic.db"
    for mod in (people_store, dept_store, episodic_module, agent_overrides, session_store):
        monkeypatch.setattr(mod, "DB_PATH", db_path)
    people_store.initialize_db()
    dept_store.initialize_db()
    agent_overrides.initialize_overrides_db()
    episodic_module.initialize_db(db_path)
    people_registry.invalidate()
    dept_registry.invalidate()
    return db_path


@pytest.fixture()
def people(db: Path) -> dict[str, int]:
    return {
        "alex": people_store.upsert_person(full_name="Alex Owner", is_principal=True, email="alex@example.com"),
        "sabin": people_store.upsert_person(full_name="Sabin Teammate", email="sabin@example.com"),
    }


@pytest.fixture()
def client(db: Path) -> TestClient:
    app = FastAPI()
    app.include_router(people_route.router)
    return TestClient(app)


def _email(pid: int) -> str | None:
    p = people_store.get_person(pid)
    return p.email if p else None


# --------------------------------------------------------------------- HTTP routes


def test_a_teammate_cannot_put_their_email_on_the_owners_entry(client: TestClient, people: dict[str, int]) -> None:
    """The takeover: the People list decides who is the owner at sign-in."""
    resp = client.patch(f"/people/{people['alex']}", json={"email": "sabin@example.com"}, headers=SABIN)
    assert resp.status_code == 403
    assert resp.json() == {"detail": "Only the principal can change the People list"}
    assert _email(people["alex"]) == "alex@example.com"


def test_a_teammate_cannot_add_a_second_principal(client: TestClient, people: dict[str, int]) -> None:
    resp = client.post("/people", json={"full_name": "Second Owner", "is_principal": True, "email": "evil@example.com"}, headers=SABIN)
    assert resp.status_code == 403
    assert [p.full_name for p in people_store.list_people()] == ["Alex Owner", "Sabin Teammate"]
    assert people_store.find_principal_person().id == people["alex"]


def test_a_teammate_cannot_archive_the_owner(client: TestClient, people: dict[str, int]) -> None:
    assert client.post(f"/people/{people['alex']}/archive", headers=SABIN).status_code == 403
    assert not people_store.get_person(people["alex"]).archived


def test_nobody_edits_their_own_entry_either(client: TestClient, people: dict[str, int]) -> None:
    """Its email and chat ids are how they sign in and are reached; the rest decides approvals."""
    resp = client.patch(f"/people/{people['sabin']}", json={"role": "CEO", "email": "elsewhere@example.com"}, headers=SABIN)
    assert resp.status_code == 403
    assert _email(people["sabin"]) == "sabin@example.com"


def test_a_signed_in_user_who_is_not_on_the_roster_is_refused(client: TestClient, people: dict[str, int]) -> None:
    assert client.post("/people", json={"full_name": "X"}, headers=STRANGER).status_code == 403
    assert client.patch(f"/people/{people['alex']}", json={"role": "x"}, headers=STRANGER).status_code == 403


def test_the_principal_can_still_manage_the_roster(client: TestClient, people: dict[str, int]) -> None:
    created = client.post("/people", json={"full_name": "New Hire", "email": "new@example.com"}, headers=ALEX)
    assert created.status_code == 201
    pid = created.json()["id"]
    assert client.patch(f"/people/{pid}", json={"role": "Analyst"}, headers=ALEX).status_code == 200
    assert client.post(f"/people/{pid}/archive", headers=ALEX).status_code == 204


def test_a_request_with_no_caller_header_is_the_owner(client: TestClient, people: dict[str, int]) -> None:
    """CLI or direct curl holding the shared secret."""
    assert client.post("/people", json={"full_name": "Via CLI"}).status_code == 201


def test_first_setup_works_before_anyone_is_the_principal(client: TestClient, db: Path) -> None:
    """No principal yet: anyone may add the owner. Once there is one, the door closes."""
    first = client.post("/people", json={"full_name": "Founder", "is_principal": True, "email": "founder@example.com"}, headers=STRANGER)
    assert first.status_code == 201
    late = client.post("/people", json={"full_name": "Late"}, headers=STRANGER)
    assert late.status_code == 403


def test_a_refusal_reveals_nothing_about_which_ids_exist(client: TestClient, people: dict[str, int]) -> None:
    real = client.patch(f"/people/{people['alex']}", json={"role": "x"}, headers=SABIN)
    fake = client.patch("/people/999999", json={"role": "x"}, headers=SABIN)
    assert (real.status_code, real.json()) == (fake.status_code, fake.json()) == (403, {"detail": "Only the principal can change the People list"})
    assert client.post("/people/999999/archive", headers=SABIN).status_code == 403


def test_the_check_fails_closed_when_the_roster_cannot_be_read(client: TestClient, people: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("people db unreadable")

    monkeypatch.setattr(people_store, "find_principal_person", boom)
    assert client.post("/people", json={"full_name": "X"}, headers=ALEX).status_code == 403


def test_reads_stay_open_to_everyone_signed_in(client: TestClient, people: dict[str, int]) -> None:
    assert client.get("/people", headers=SABIN).status_code == 200
    assert client.get(f"/people/{people['alex']}", headers=SABIN).status_code == 200


# ------------------------------------------------------------ the Executive's roster tools


def _run(tool: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(asyncio.run(tool(payload)))


@pytest.fixture()
def speaker() -> Any:
    """Record who is speaking for one tool call, the way the web chat does."""
    def _set(caller: TurnCaller | None) -> None:
        current_turn_caller.set(caller)

    token = current_turn_caller.set(None)
    yield _set
    current_turn_caller.reset(token)


ROSTER_TOOLS = [
    (people_tools.handle_upsert_person, {"full_name": "Planted Person", "email": "planted@example.com"}),
    (people_tools.handle_archive_person, {"person_id": 1}),
    (people_tools.handle_set_department_head, {"department_slug": "finance", "person_id": 1}),
]


@pytest.mark.parametrize("tool,payload", ROSTER_TOOLS, ids=["upsert", "archive", "set_head"])
def test_a_teammate_cannot_change_the_roster_through_the_executive(tool: Any, payload: dict[str, Any], people: dict[str, int], speaker: Any) -> None:
    speaker(TurnCaller(person_id=people["sabin"], from_web_chat=True))
    result = _run(tool, payload)
    assert result["status"] == "refused"
    assert "owner" in result["detail"]
    assert [p.full_name for p in people_store.list_people()] == ["Alex Owner", "Sabin Teammate"]


@pytest.mark.parametrize("tool,payload", ROSTER_TOOLS, ids=["upsert", "archive", "set_head"])
def test_a_turn_with_no_recorded_speaker_is_refused(tool: Any, payload: dict[str, Any], people: dict[str, int], speaker: Any) -> None:
    """Channel adapters, the scheduler, workflows, alert review, the CLI and the MCP
    server never record one, so they fail closed."""
    speaker(None)
    assert _run(tool, payload)["status"] == "refused"


def test_even_the_principal_is_refused_off_the_verified_web_surface(people: dict[str, int], speaker: Any) -> None:
    speaker(TurnCaller(person_id=people["alex"], from_web_chat=False))
    assert _run(people_tools.handle_upsert_person, {"full_name": "Via Email"})["status"] == "refused"
    assert len(people_store.list_people()) == 2


def test_a_signed_in_user_not_on_the_roster_is_refused_by_the_tools(people: dict[str, int], speaker: Any) -> None:
    speaker(TurnCaller(person_id=None, from_web_chat=True))
    result = _run(people_tools.handle_upsert_person, {"full_name": "X"})
    assert result["status"] == "refused" and "not on anyone's People entry" in result["detail"]


def test_the_principal_on_the_web_chat_can_still_use_the_tools(people: dict[str, int], speaker: Any) -> None:
    speaker(TurnCaller(person_id=people["alex"], from_web_chat=True))
    result = _run(people_tools.handle_upsert_person, {"full_name": "Cindy Lee", "role": "Marketing"})
    assert result.get("status") != "refused" and "error" not in result
    assert "Cindy Lee" in [p.full_name for p in people_store.list_people()]


def test_a_refused_roster_change_is_audited(people: dict[str, int], speaker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(people_tools, "_audit", lambda *a, **k: seen.append(a))
    speaker(TurnCaller(person_id=people["sabin"], from_web_chat=True))
    _run(people_tools.handle_archive_person, {"person_id": people["alex"]})
    assert seen and seen[0][0] == "archive_person" and seen[0][2] is False


def test_the_tools_that_only_read_are_not_gated(people: dict[str, int], speaker: Any) -> None:
    """Only the writers are refused: with no recorded speaker at all, listing the
    roster still returns it."""
    speaker(None)
    listed = _run(people_tools.handle_list_people, {})
    assert "status" not in listed if isinstance(listed, dict) else True
    text = json.dumps(listed)
    assert "Alex Owner" in text and "Sabin Teammate" in text and "refused" not in text


# ------------------------------------------------ the web chat records who is speaking


def test_the_web_chat_records_the_verified_speaker_for_the_tools(db: Path, people: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate is only as good as what the chat path records: a teammate's turn
    must carry the teammate, not the principal."""
    from openexecutive.knowledge import retriever
    from openexecutive.onboarding import profile_builder
    from openexecutive.orchestrator import executive as exec_mod
    from openexecutive.utils import session_title

    monkeypatch.setattr(profile_builder, "load_or_create_profile", lambda: CompanyProfile())
    monkeypatch.setattr(retriever, "retrieve", lambda *_a, **_k: "")

    async def _no_title(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(session_title, "generate_session_title", _no_title)
    seen: list[TurnCaller | None] = []

    class _Stub:
        _THINKING = exec_mod.Executive._THINKING

        def __init__(self, **_kw: Any) -> None:
            pass

        async def stream_chat(self, **_kw: Any) -> AsyncIterator[str]:
            seen.append(current_turn_caller.get())
            yield "ok"

    monkeypatch.setattr(exec_mod, "Executive", _Stub)
    app = FastAPI()
    app.include_router(chat_route.router)
    c = TestClient(app)
    chat_route._sessions.clear()

    for headers in (SABIN, ALEX, STRANGER):
        _ = c.post("/chat", json={"message": "hi"}, headers=headers).text

    assert seen == [
        TurnCaller(person_id=people["sabin"], from_web_chat=True),
        TurnCaller(person_id=people["alex"], from_web_chat=True),
        TurnCaller(person_id=None, from_web_chat=True),
    ]


# ------------------------------------- the routes that wipe or swap the roster (bypass)


@pytest.fixture()
def full_client(db: Path) -> TestClient:
    from openexecutive.api.routes import clients as clients_route
    from openexecutive.api.routes import departments as departments_route
    from openexecutive.api.routes import fixtures as fixtures_route

    app = FastAPI()
    for r in (people_route.router, fixtures_route.router, clients_route.router, departments_route.router):
        app.include_router(r)
    return TestClient(app)


STATE_SWAPPING_ROUTES = [
    ("POST", "/fixtures/snapshot", None),
    ("POST", "/fixtures/reset", None),
    ("POST", "/fixtures/unload", None),
    ("POST", "/fixtures/clearpath_health/load", None),
    ("POST", "/fixtures/generate", {"description": "x"}),
    ("POST", "/fixtures", {"bundle": {}}),
    ("DELETE", "/fixtures/some-fixture", None),
    ("POST", "/clients", {"display_name": "Acme", "source": "blank"}),
    ("POST", "/clients/save", None),
    ("POST", "/clients/acme/activate", None),
    ("PATCH", "/clients/acme", {"display_name": "x"}),
    ("DELETE", "/clients/acme", None),
]


@pytest.mark.parametrize("method,path,body", STATE_SWAPPING_ROUTES, ids=[f"{m} {p}" for m, p, _ in STATE_SWAPPING_ROUTES])
def test_a_teammate_cannot_wipe_or_swap_the_companys_data(full_client: TestClient, people: dict[str, int], method: str, path: str, body: Any) -> None:
    """A reset or a blank client empties the roster; with no principal the install
    counts as unclaimed, so an ungated swap lets a teammate wipe it and then add
    themselves as principal."""
    resp = full_client.request(method, path, json=body, headers=SABIN)
    assert resp.status_code == 403
    assert resp.json()["detail"].startswith("Only the principal can switch the company data")


def test_the_wipe_then_claim_chain_is_broken_at_the_first_step(full_client: TestClient, people: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the install, then add yourself as principal. The reset must be refused
    before anything runs, so the owner is still the principal afterwards."""
    from openexecutive.cli import fixture_loader

    def must_not_run(*_a: Any, **_k: Any) -> None:
        raise AssertionError("reset_all_state ran for a teammate")

    monkeypatch.setattr(fixture_loader, "reset_all_state", must_not_run)

    assert full_client.post("/fixtures/reset", headers=SABIN).status_code == 403
    claim = full_client.post("/people", json={"full_name": "Sabin", "is_principal": True, "email": "sabin@example.com"}, headers=SABIN)
    assert claim.status_code == 403
    assert people_store.find_principal_person().id == people["alex"]


def test_only_the_department_head_field_is_the_owners_call(full_client: TestClient, people: dict[str, int]) -> None:
    from openexecutive.departments import store as dept_store

    dept_store.seed_default_departments()
    assert full_client.patch("/departments/finance", json={"head_person_id": people["sabin"]}, headers=SABIN).status_code == 403
    assert dept_store.get_department("finance").config.head_person_id != people["sabin"]
    # everything else about a department stays as open as before
    assert full_client.patch("/departments/finance", json={"title": "Finance & Ops"}, headers=SABIN).status_code == 200
    assert full_client.patch("/departments/finance", json={"head_person_id": people["sabin"]}, headers=ALEX).status_code == 200


def test_reading_fixtures_and_clients_stays_open(full_client: TestClient, people: dict[str, int]) -> None:
    assert full_client.get("/fixtures", headers=SABIN).status_code == 200
    assert full_client.get("/clients", headers=SABIN).status_code == 200


# --------------------- workflows and the setup wizard that also write the roster (B3, B4)


def test_the_roster_writing_workflows_are_declared_and_exist() -> None:
    from openexecutive.workflows import ROSTER_WRITING_WORKFLOWS, WORKFLOW_REGISTRY

    assert ROSTER_WRITING_WORKFLOWS == {"new_hire_onboarding"}
    assert ROSTER_WRITING_WORKFLOWS <= set(WORKFLOW_REGISTRY)


@pytest.fixture()
def wf_client(db: Path) -> TestClient:
    from openexecutive.api.routes import onboarding as onboarding_route
    from openexecutive.api.routes import workflows as workflows_route

    app = FastAPI()
    app.include_router(workflows_route.router)
    app.include_router(onboarding_route.router)
    return TestClient(app)


def test_a_teammate_cannot_start_the_new_hire_workflow_over_http(wf_client: TestClient, people: dict[str, int]) -> None:
    """The workflow adds or updates a People row from candidate data. It is refused
    before the body is even read, so nothing runs."""
    for body in ({}, {"candidate_id": 1}, "not json"):
        resp = wf_client.post("/workflows/new_hire_onboarding/runs", content=json.dumps(body), headers={**SABIN, "content-type": "application/json"})
        assert resp.status_code == 403
    assert [p.full_name for p in people_store.list_people()] == ["Alex Owner", "Sabin Teammate"]


def test_the_owner_is_not_blocked_from_the_new_hire_workflow(wf_client: TestClient, people: dict[str, int]) -> None:
    """Past the gate the route validates the body: an empty one is a 422, and nothing runs."""
    resp = wf_client.post("/workflows/new_hire_onboarding/runs", json={}, headers=ALEX)
    assert resp.status_code == 422


def test_other_workflows_are_not_gated(wf_client: TestClient, people: dict[str, int]) -> None:
    assert wf_client.post("/workflows/no_such_workflow/runs", json={}, headers=SABIN).status_code == 404


@pytest.mark.parametrize("tool,payload", [
    (lambda: __import__("openexecutive.orchestrator.talent_tools", fromlist=["x"]).handle_start_talent_workflow,
     {"workflow": "new_hire_onboarding", "inputs": {"candidate_id": 1}}),
    (lambda: __import__("openexecutive.orchestrator.workflow_run_tools", fromlist=["x"]).handle_run_workflow,
     {"workflow": "new_hire_onboarding", "inputs": {"candidate_id": 1}}),
], ids=["start_talent_workflow", "run_workflow"])
def test_the_executives_workflow_tools_will_not_start_the_new_hire_workflow_for_a_teammate(tool: Any, payload: dict[str, Any], people: dict[str, int], speaker: Any) -> None:
    speaker(TurnCaller(person_id=people["sabin"], from_web_chat=True))
    assert _run(tool(), payload)["status"] == "refused"
    speaker(None)  # channel adapter, scheduler, CLI: no recorded speaker
    assert _run(tool(), payload)["status"] == "refused"
    assert [p.full_name for p in people_store.list_people()] == ["Alex Owner", "Sabin Teammate"]


def test_a_teammate_cannot_run_the_setup_wizard_once_there_is_an_owner(wf_client: TestClient, people: dict[str, int]) -> None:
    """Completing it creates People rows with emails (sign-in access) and a second principal."""
    resp = wf_client.post("/onboard/answer", json={"session_id": "s", "answer": "x"}, headers=SABIN)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Only the principal can run the setup wizard"


def test_the_owner_and_a_fresh_install_can_still_run_the_wizard(wf_client: TestClient, people: dict[str, int], db: Path) -> None:
    """Past the gate this fake session is a 404, which proves the request got through."""
    assert wf_client.post("/onboard/answer", json={"session_id": "s", "answer": "x"}, headers=ALEX).status_code == 404
    with people_store._get_conn() as conn:
        conn.execute("DELETE FROM people")  # a fresh install: nobody is the principal yet
    people_registry.invalidate()
    assert wf_client.post("/onboard/answer", json={"session_id": "s", "answer": "x"}, headers=STRANGER).status_code == 404


# ---- the workflow's own person matching can no longer land on the owner's row


def _hire_ctx(name: str, email: str | None, role: str = "Engineer") -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        candidate=SimpleNamespace(full_name=name, email=email),
        engagement=SimpleNamespace(role_title=role, department=""),
    )


def test_a_candidate_who_shares_the_owners_name_cannot_take_over_the_owners_row(db: Path) -> None:
    """The wizard's principal row has no email. A candidate with the same name and
    an attacker's email used to be matched BY NAME and get that email written onto
    the owner's entry, which the sign-in allow-list then trusts."""
    from openexecutive.workflows.new_hire_onboarding import _upsert_hire_person

    owner = people_store.upsert_person(full_name="Alex Owner", role="Founder", is_principal=True)
    assert people_store.get_person(owner).email is None

    pid, created = _upsert_hire_person(_hire_ctx("Alex Owner", "attacker@evil.example"))

    # The owner's entry is untouched. (A separate ordinary row is created for the
    # candidate: adding a person is the owner's decision, which is what the gate on
    # starting this workflow protects. This function only guarantees it can never
    # EDIT the principal's row.)
    row = people_store.get_person(owner)
    assert row.email is None and row.role == "Founder" and row.is_principal
    assert (pid != owner) and created is True
    assert people_store.find_principal_person().id == owner
    assert people_store.find_person_by_email("attacker@evil.example").id == pid
    assert not people_store.get_person(pid).is_principal


def test_onboarding_never_edits_the_owners_row_even_when_matched_by_email(db: Path) -> None:
    from openexecutive.workflows.new_hire_onboarding import _upsert_hire_person

    owner = people_store.upsert_person(full_name="Alex Owner", role="Founder", is_principal=True, email="alex@example.com")
    pid, created = _upsert_hire_person(_hire_ctx("Someone Else", "alex@example.com", role="Intern"))
    assert (pid, created) == (owner, False)
    assert people_store.get_person(owner).role == "Founder"


def test_a_same_named_ordinary_person_is_still_matched_and_updated(db: Path) -> None:
    from openexecutive.workflows.new_hire_onboarding import _upsert_hire_person

    owner = people_store.upsert_person(full_name="Sam Lee", is_principal=True, email="sam@example.com")
    hire = people_store.upsert_person(full_name="Sam Lee", role="Contractor")
    pid, created = _upsert_hire_person(_hire_ctx("Sam Lee", None, role="Engineer"))
    assert (pid, created) == (hire, False), "matched the ordinary row, not the owner's"
    assert people_store.get_person(hire).role == "Engineer" and people_store.get_person(owner).role != "Engineer"


def test_a_genuinely_new_hire_still_creates_a_person(db: Path) -> None:
    from openexecutive.workflows.new_hire_onboarding import _upsert_hire_person

    pid, created = _upsert_hire_person(_hire_ctx("Brand New", "new@example.com"))
    assert created is True and people_store.get_person(pid).email == "new@example.com"
