"""Issue #50: at most one active principal at a time.

`is_principal` can only be set at creation (PersonPatch has no such field, and
the Executive's roster tools refuse it outright), so `upsert_person`'s INSERT
path is the one place a second active principal could appear.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import people as people_route
from openexecutive.people import registry as people_registry
from openexecutive.people import store as people_store


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
