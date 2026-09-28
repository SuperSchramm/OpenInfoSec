"""Issue #50: a candidate's email is the one talent field that can turn into
sign-in access (via new_hire_onboarding), so setting or changing it is the
principal's alone -- every other candidate/engagement field stays open."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import talent as talent_route
from openexecutive.people import store as people_store
from openexecutive.talent import store as talent_store

ALEX = {"x-caller-email": "alex@example.com"}  # principal
SABIN = {"x-caller-email": "sabin@example.com"}  # rostered teammate
STRANGER = {"x-caller-email": "stranger@example.com"}  # signed in, not rostered


class _NoopStore:
    def add_documents(self, *a, **k) -> None: ...
    def delete_documents(self, *a, **k) -> None: ...
    def query(self, *a, **k):  # noqa: ANN001, ANN201
        return []


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(talent_store, "DB_PATH", tmp_path / "talent.db")
    talent_store.initialize_db()
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "people.db")
    people_store.initialize_db()
    people_store.upsert_person(full_name="Alex", is_principal=True, email="alex@example.com")
    people_store.upsert_person(full_name="Sabin", email="sabin@example.com")

    app = FastAPI()
    app.include_router(talent_route.router)
    app.state.store = _NoopStore()
    return TestClient(app)


def _engagement(client: TestClient) -> int:
    resp = client.post(
        "/engagements",
        json={"role_title": "VP Drilling", "department": "Drilling", "must_haves": "x"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_teammate_cannot_set_email_on_create(client: TestClient) -> None:
    eid = _engagement(client)
    body = {"engagement_id": eid, "full_name": "Cam Doe", "email": "cam@example.com"}
    for h in (SABIN, STRANGER):
        assert client.post("/candidates", json=body, headers=h).status_code == 403
    assert client.get("/candidates").json() == []  # nothing was created


def test_teammate_can_create_a_candidate_with_no_email(client: TestClient) -> None:
    eid = _engagement(client)
    body = {"engagement_id": eid, "full_name": "Cam Doe"}
    resp = client.post("/candidates", json=body, headers=SABIN)
    assert resp.status_code == 201
    assert resp.json()["email"] is None


def test_owner_and_headerless_caller_can_set_email_on_create(client: TestClient) -> None:
    eid = _engagement(client)
    for h in (ALEX, None):
        body = {"engagement_id": eid, "full_name": "Cam Doe", "email": "cam@example.com"}
        resp = client.post("/candidates", json=body, headers=h)
        assert resp.status_code == 201
        assert resp.json()["email"] == "cam@example.com"


def test_teammate_cannot_add_an_email_via_patch(client: TestClient) -> None:
    eid = _engagement(client)
    cid = client.post(
        "/candidates", json={"engagement_id": eid, "full_name": "Cam Doe"}, headers=SABIN
    ).json()["id"]
    resp = client.patch(f"/candidates/{cid}", json={"email": "cam@example.com"}, headers=SABIN)
    assert resp.status_code == 403
    assert client.get(f"/candidates/{cid}").json()["email"] is None


def test_teammate_can_edit_other_fields_and_clear_an_existing_email(client: TestClient) -> None:
    eid = _engagement(client)
    cid = client.post(
        "/candidates",
        json={"engagement_id": eid, "full_name": "Cam Doe", "email": "cam@example.com"},
        headers=ALEX,
    ).json()["id"]
    # Ordinary field: unrestricted.
    resp = client.patch(f"/candidates/{cid}", json={"current_title": "Staff Eng"}, headers=SABIN)
    assert resp.status_code == 200
    assert resp.json()["current_title"] == "Staff Eng"
    # Re-sending the SAME email is a no-op, not a "set": unrestricted too.
    resp = client.patch(f"/candidates/{cid}", json={"email": "cam@example.com"}, headers=SABIN)
    assert resp.status_code == 200
    # Clearing an email removes access rather than granting it: unrestricted.
    resp = client.patch(f"/candidates/{cid}", json={"email": None}, headers=SABIN)
    assert resp.status_code == 200
    assert resp.json()["email"] is None


def test_owner_can_change_an_email_via_patch(client: TestClient) -> None:
    eid = _engagement(client)
    cid = client.post(
        "/candidates", json={"engagement_id": eid, "full_name": "Cam Doe"}, headers=ALEX
    ).json()["id"]
    resp = client.patch(f"/candidates/{cid}", json={"email": "cam@example.com"}, headers=ALEX)
    assert resp.status_code == 200
    assert resp.json()["email"] == "cam@example.com"
