"""Issue #50: at most one active principal at a time.

`is_principal` can only be set at creation (PersonPatch has no such field, and
the Executive's roster tools refuse it outright), so `upsert_person`'s INSERT
path is the one place a second active principal could appear.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import people as people_route
from openexecutive.people import registry as people_registry
from openexecutive.people import store as people_store

ALEX = {"x-caller-email": "alex@example.com"}
SABIN = {"x-caller-email": "sabin@example.com"}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "people.db")
    people_registry.invalidate()
    people_store.initialize_db()
    app = FastAPI()
    app.include_router(people_route.router)
    return TestClient(app)


def test_store_refuses_a_second_active_principal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "p.db")
    people_store.initialize_db()
    people_store.upsert_person(full_name="Alex", is_principal=True)
    with pytest.raises(ValueError, match="principal already exists"):
        people_store.upsert_person(full_name="Sabin", is_principal=True)
    assert len(people_store.list_people()) == 1


def test_store_allows_a_new_principal_once_none_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "p.db")
    people_store.initialize_db()
    alex = people_store.upsert_person(full_name="Alex", is_principal=True)
    people_store.archive_person(alex)  # unclaimed again
    sabin = people_store.upsert_person(full_name="Sabin", is_principal=True)
    assert people_store.get_person(sabin).is_principal is True


def test_route_returns_409_for_a_second_principal(client: TestClient) -> None:
    resp = client.post("/people", json={"full_name": "Alex", "is_principal": True})
    assert resp.status_code == 201
    resp = client.post("/people", json={"full_name": "Sabin", "is_principal": True})
    assert resp.status_code == 409
    assert [p["full_name"] for p in client.get("/people").json()] == ["Alex"]


def test_route_does_not_block_ordinary_people_or_a_second_principal_attempt_after_archive(
    client: TestClient,
) -> None:
    alex = client.post("/people", json={"full_name": "Alex", "is_principal": True}).json()["id"]
    # ordinary, non-principal people are unaffected
    assert client.post("/people", json={"full_name": "Sabin"}).status_code == 201
    client.post(f"/people/{alex}/archive")
    resp = client.post("/people", json={"full_name": "Rio", "is_principal": True})
    assert resp.status_code == 201


def test_concurrent_principal_creation_is_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #50 (review finding): two threads racing POST /people with
    is_principal=True while the install is unclaimed must not both succeed --
    BEGIN IMMEDIATE serializes the check-then-insert against SQLite's own
    single-writer lock."""
    import threading

    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "race.db")
    people_store.initialize_db()

    results: list[bool] = []
    barrier = threading.Barrier(8)

    def attempt() -> None:
        barrier.wait()
        try:
            people_store.upsert_person(full_name="Racer", is_principal=True)
            results.append(True)
        except ValueError:
            results.append(False)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    active_principals = [
        p for p in people_store.list_people() if p.is_principal and not p.archived
    ]
    assert len(active_principals) == 1


# --------------------------------------------------------------------------- #
# transfer_principal — no unclaimed window (issue #53)
# --------------------------------------------------------------------------- #

def test_transfer_principal_moves_the_flag_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "t.db")
    people_store.initialize_db()
    alex = people_store.upsert_person(full_name="Alex", is_principal=True)
    sabin = people_store.upsert_person(full_name="Sabin")

    previous = people_store.transfer_principal(sabin)
    assert previous == alex
    assert people_store.get_person(alex).is_principal is False
    assert people_store.get_person(sabin).is_principal is True
    # No window where nobody is principal, and never two at once.
    active = [p for p in people_store.list_people() if p.is_principal and not p.archived]
    assert [p.id for p in active] == [sabin]


def test_transfer_principal_refuses_with_no_active_principal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "t.db")
    people_store.initialize_db()
    sabin = people_store.upsert_person(full_name="Sabin")
    with pytest.raises(ValueError, match="no active principal"):
        people_store.transfer_principal(sabin)


def test_transfer_principal_refuses_an_archived_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "t.db")
    people_store.initialize_db()
    people_store.upsert_person(full_name="Alex", is_principal=True)
    sabin = people_store.upsert_person(full_name="Sabin")
    people_store.archive_person(sabin)
    with pytest.raises(ValueError, match="archived"):
        people_store.transfer_principal(sabin)


def test_transfer_principal_refuses_an_unknown_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "t.db")
    people_store.initialize_db()
    people_store.upsert_person(full_name="Alex", is_principal=True)
    with pytest.raises(ValueError, match="not found"):
        people_store.transfer_principal(99999)


def test_transfer_principal_refuses_transferring_to_self(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "t.db")
    people_store.initialize_db()
    alex = people_store.upsert_person(full_name="Alex", is_principal=True)
    with pytest.raises(ValueError, match="already the principal"):
        people_store.transfer_principal(alex)


def test_transfer_principal_route_is_owner_only(client: TestClient) -> None:
    alex = client.post(
        "/people", json={"full_name": "Alex", "is_principal": True, "email": "alex@example.com"}
    ).json()["id"]
    sabin = client.post(
        "/people", json={"full_name": "Sabin", "email": "sabin@example.com"}, headers=ALEX
    ).json()["id"]

    resp = client.post(f"/people/{sabin}/transfer-principal", headers=SABIN)
    assert resp.status_code == 403
    assert client.get(f"/people/{alex}").json()["is_principal"] is True

    resp = client.post(f"/people/{sabin}/transfer-principal", headers=ALEX)
    assert resp.status_code == 200
    assert resp.json()["is_principal"] is True
    assert client.get(f"/people/{alex}").json()["is_principal"] is False

    # The old owner is no longer principal, so they can't transfer it back.
    resp = client.post(f"/people/{alex}/transfer-principal", headers=ALEX)
    assert resp.status_code == 403


def test_transfer_principal_route_conflict_and_not_found(client: TestClient) -> None:
    alex = client.post(
        "/people", json={"full_name": "Alex", "is_principal": True, "email": "alex@example.com"}
    ).json()["id"]
    assert client.post(f"/people/{alex}/transfer-principal", headers=ALEX).status_code == 409
    assert client.post("/people/99999/transfer-principal", headers=ALEX).status_code == 404


def test_transfer_principal_clears_every_active_principal_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #53 (review finding): the schema does not enforce a single
    principal (idx_people_principal is not UNIQUE). If a pre-existing
    duplicate somehow exists, a transfer must not leave it behind."""
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "t.db")
    people_store.initialize_db()
    alex = people_store.upsert_person(full_name="Alex", is_principal=True)
    # Simulate a corrupted/duplicate state directly, bypassing the app-level
    # uniqueness guard on upsert_person (which only applies to new inserts).
    with sqlite3.connect(tmp_path / "t.db") as conn:
        conn.execute(
            "INSERT INTO people (full_name, role, is_principal, department_slugs_json, "
            "preferred_channel, response_sla_hours, created_at, updated_at) "
            "VALUES ('Bob', '', 1, '[]', 'any', 24, '2026-01-01', '2026-01-01')"
        )
        bob = conn.execute("SELECT id FROM people WHERE full_name = 'Bob'").fetchone()[0]
    carol = people_store.upsert_person(full_name="Carol")

    people_store.transfer_principal(carol)

    active = [p for p in people_store.list_people() if p.is_principal and not p.archived]
    assert [p.id for p in active] == [carol]
    assert people_store.get_person(alex).is_principal is False
    assert people_store.get_person(bob).is_principal is False


def test_transfer_principal_refuses_when_expected_current_principal_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #53 (review finding): the HTTP route only confirms the caller was
    principal when ITS request started; the store must re-check inside the
    write transaction so a second racing request can't strip ownership from
    whoever the first one just promoted."""
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "t.db")
    people_store.initialize_db()
    alex = people_store.upsert_person(full_name="Alex", is_principal=True)
    sabin = people_store.upsert_person(full_name="Sabin")
    rio = people_store.upsert_person(full_name="Rio")

    # First request (Alex -> Sabin) already landed by the time this one runs.
    people_store.transfer_principal(sabin)

    with pytest.raises(ValueError, match="already changed"):
        people_store.transfer_principal(rio, expected_current_principal_id=alex)

    assert people_store.get_person(sabin).is_principal is True  # untouched


def test_transfer_principal_accepts_a_matching_expectation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "t.db")
    people_store.initialize_db()
    alex = people_store.upsert_person(full_name="Alex", is_principal=True)
    sabin = people_store.upsert_person(full_name="Sabin")

    people_store.transfer_principal(sabin, expected_current_principal_id=alex)
    assert people_store.get_person(sabin).is_principal is True


def test_transfer_principal_route_racing_requests_only_the_first_wins(
    client: TestClient,
) -> None:
    client.post(
        "/people", json={"full_name": "Alex", "is_principal": True, "email": "alex@example.com"}
    )
    sabin = client.post(
        "/people", json={"full_name": "Sabin", "email": "sabin@example.com"}, headers=ALEX
    ).json()["id"]
    rio = client.post(
        "/people", json={"full_name": "Rio", "email": "rio@example.com"}, headers=ALEX
    ).json()["id"]

    first = client.post(f"/people/{sabin}/transfer-principal", headers=ALEX)
    assert first.status_code == 200
    # A second request that still believes Alex is principal (e.g. it was
    # in flight before the first one landed) must not silently demote Sabin.
    second = client.post(f"/people/{rio}/transfer-principal", headers=ALEX)
    assert second.status_code == 403  # Alex is no longer the principal at all
    assert client.get(f"/people/{sabin}").json()["is_principal"] is True
