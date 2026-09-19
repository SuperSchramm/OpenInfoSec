"""Regression tests for issue #39: bounded, symlink-safe writes to the knowledge overlay.

Before, the routes checked ``realpath`` containment and then wrote in a separate
step (a swap window), and ``content`` had no size bound while embedding runs
synchronously on the event loop and the bytes now persist on the shared volume.
"""
from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openexecutive.knowledge import loader as loader_mod
from openexecutive.knowledge import overlay_fs
from openexecutive.knowledge.loader import MAX_KNOWLEDGE_DOC_CHARS, builtin_overlay_root, seed_builtin_knowledge
from openexecutive.knowledge.store import ChromaDBStore

pytestmark = pytest.mark.skipif(not overlay_fs._SAFE, reason="needs O_NOFOLLOW and dir_fd support")


# ------------------------------------------------------------------ the helpers

def test_write_creates_nested_directories_and_content(tmp_path: Path) -> None:
    overlay_fs.write_text(tmp_path / "ov", Path("failures/legal/x.md"), "héllo")
    assert (tmp_path / "ov" / "failures" / "legal" / "x.md").read_text(encoding="utf-8") == "héllo"


def test_write_replaces_and_truncates(tmp_path: Path) -> None:
    overlay_fs.write_text(tmp_path, Path("a/x.md"), "long content here")
    overlay_fs.write_text(tmp_path, Path("a/x.md"), "short")
    assert (tmp_path / "a" / "x.md").read_text(encoding="utf-8") == "short"


def test_a_symlinked_final_component_is_refused_and_its_target_untouched(tmp_path: Path) -> None:
    ov, secret = tmp_path / "ov", tmp_path / "secret.md"
    secret.write_text("keep", encoding="utf-8")
    (ov / "a").mkdir(parents=True)
    (ov / "a" / "x.md").symlink_to(secret)
    with pytest.raises(overlay_fs.UnsafeOverlayPath):
        overlay_fs.write_text(ov, Path("a/x.md"), "overwritten")
    assert secret.read_text(encoding="utf-8") == "keep"


def test_a_symlinked_parent_directory_is_refused_and_nothing_is_written_outside(tmp_path: Path) -> None:
    ov, outside = tmp_path / "ov", tmp_path / "outside"
    ov.mkdir()
    outside.mkdir()
    (ov / "a").symlink_to(outside)
    with pytest.raises(overlay_fs.UnsafeOverlayPath):
        overlay_fs.write_text(ov, Path("a/b/x.md"), "x")
    with pytest.raises(overlay_fs.UnsafeOverlayPath):
        overlay_fs.touch(ov, Path("a/x.md.deleted"))
    with pytest.raises(overlay_fs.UnsafeOverlayPath):
        overlay_fs.unlink(ov, Path("a/x.md"))
    assert not list(outside.rglob("*"))


def test_a_file_where_a_directory_belongs_is_refused(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("i am a file", encoding="utf-8")
    with pytest.raises(overlay_fs.UnsafeOverlayPath):
        overlay_fs.write_text(tmp_path, Path("a/x.md"), "x")


def test_unlink_removes_a_symlink_itself_never_its_target(tmp_path: Path) -> None:
    ov, target = tmp_path / "ov", tmp_path / "target.md"
    target.write_text("keep", encoding="utf-8")
    (ov / "a").mkdir(parents=True)
    (ov / "a" / "x.md").symlink_to(target)
    overlay_fs.unlink(ov, Path("a/x.md"))
    assert not (ov / "a" / "x.md").is_symlink()
    assert target.read_text(encoding="utf-8") == "keep"


def test_unlink_of_something_that_is_not_there_is_a_no_op(tmp_path: Path) -> None:
    overlay_fs.unlink(tmp_path / "no-root", Path("a/x.md"))
    (tmp_path / "ov").mkdir()
    overlay_fs.unlink(tmp_path / "ov", Path("a/b/x.md"))
    overlay_fs.unlink(tmp_path / "ov", Path("x.md"))


def test_total_bytes_counts_real_files_only(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.md").write_bytes(b"12345")
    (tmp_path / "y.md").write_bytes(b"123")
    outside = tmp_path.parent / "big-outside.md"
    outside.write_bytes(b"x" * 1000)
    (tmp_path / "link.md").symlink_to(outside)
    assert overlay_fs.total_bytes(tmp_path) == 8
    assert overlay_fs.total_bytes(tmp_path / "missing") == 0


# ------------------------------------------------------------------ the routes

class _FakeReviewStore:
    def register(self, **_kw: Any) -> None: ...
    def touch_modified(self, *_a: Any) -> None: ...
    def delete_item(self, *_a: Any) -> None: ...


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from openexecutive.api.main import create_app

    shipped = tmp_path / "image" / "builtin"
    (shipped / "strategy").mkdir(parents=True)
    (shipped / "strategy" / "shipped_doc.md").write_text("word " * 200, encoding="utf-8")
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", shipped)
    monkeypatch.setattr(loader_mod, "FAILURES_KNOWLEDGE_PATH", shipped / "failures")
    monkeypatch.setattr("openexecutive.api.routes.knowledge.BUILTIN_KNOWLEDGE_PATH", shipped)
    monkeypatch.setattr("openexecutive.api.routes.knowledge.FAILURES_KNOWLEDGE_PATH", shipped / "failures")
    monkeypatch.setattr("openexecutive.api.routes.knowledge._get_store", lambda _request: store)
    monkeypatch.setattr("openexecutive.knowledge.review_store.ReviewStore", _FakeReviewStore)
    asyncio.run(seed_builtin_knowledge(store=store))
    return {"client": TestClient(create_app()), "store": store, "overlay": builtin_overlay_root(), "tmp": tmp_path}


def _post(client: TestClient, name: str, content: str, kind: str = "builtin") -> Any:
    return client.post(f"/knowledge/{kind}", json={"domain": "strategy", "filename": name, "content": content})


def test_a_document_over_the_size_cap_is_rejected_and_nothing_is_written(env: dict[str, Any]) -> None:
    res = _post(env["client"], "big.md", "a" * (MAX_KNOWLEDGE_DOC_CHARS + 1))
    assert res.status_code == 422
    assert not (env["overlay"] / "strategy" / "big.md").exists()


def test_a_document_exactly_at_the_cap_is_accepted(env: dict[str, Any]) -> None:
    assert _post(env["client"], "edge.md", "a" * MAX_KNOWLEDGE_DOC_CHARS).status_code == 200


def test_the_cap_applies_to_edits_and_failure_cases_too(env: dict[str, Any]) -> None:
    client = env["client"]
    too_big = "a" * (MAX_KNOWLEDGE_DOC_CHARS + 1)
    assert client.put("/knowledge/builtin/strategy/shipped_doc.md", json={"domain": "strategy", "filename": "shipped_doc.md", "content": too_big}).status_code == 422
    assert _post(client, "f.md", too_big, kind="failures").status_code == 422


def test_the_overlay_byte_budget_returns_413_and_writes_nothing(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client, overlay = env["client"], env["overlay"]
    monkeypatch.setenv("BUILTIN_OVERLAY_MAX_BYTES", "1000")
    assert _post(client, "one.md", "a" * 600).status_code == 200

    res = _post(client, "two.md", "b" * 600)

    assert res.status_code == 413
    assert not (overlay / "strategy" / "two.md").exists()


def test_replacing_a_doc_counts_only_the_growth_against_the_budget(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client = env["client"]
    monkeypatch.setenv("BUILTIN_OVERLAY_MAX_BYTES", "1000")
    _post(client, "one.md", "a" * 600)
    res = client.put("/knowledge/builtin/strategy/one.md", json={"domain": "strategy", "filename": "one.md", "content": "c" * 900})
    assert res.status_code == 200, res.text
    assert client.put("/knowledge/builtin/strategy/one.md", json={"domain": "strategy", "filename": "one.md", "content": "c" * 1100}).status_code == 413


def test_deleting_frees_budget(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client = env["client"]
    monkeypatch.setenv("BUILTIN_OVERLAY_MAX_BYTES", "1000")
    _post(client, "one.md", "a" * 600)
    assert _post(client, "two.md", "b" * 600).status_code == 413
    assert client.delete("/knowledge/builtin/strategy/one.md").status_code == 200
    assert _post(client, "two.md", "b" * 600).status_code == 200


def test_a_symlink_swapped_in_after_the_route_checked_is_still_refused(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The check-then-write window: the guard passes, then the folder becomes a symlink."""
    from openexecutive.api.routes import knowledge as routes

    client, overlay, tmp = env["client"], env["overlay"], env["tmp"]
    outside = tmp / "outside"
    outside.mkdir()
    real = routes._doc_paths

    def check_then_swap(*args: Any, **kwargs: Any) -> Any:
        paths = real(*args, **kwargs)  # passes: nothing is a symlink yet
        overlay.mkdir(parents=True, exist_ok=True)
        (overlay / "strategy").symlink_to(outside)  # ...and now the attacker swaps it
        return paths

    monkeypatch.setattr(routes, "_doc_paths", check_then_swap)

    res = _post(client, "swapped.md", "x")

    assert res.status_code == 400
    assert not list(outside.rglob("*")), "nothing written outside the overlay"


def test_a_symlink_swapped_in_before_a_delete_is_refused(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api.routes import knowledge as routes

    client, overlay, tmp = env["client"], env["overlay"], env["tmp"]
    outside = tmp / "outside"
    (outside / ".deleted" / "strategy").mkdir(parents=True)
    real = routes._doc_paths

    def check_then_swap(*args: Any, **kwargs: Any) -> Any:
        paths = real(*args, **kwargs)
        overlay.mkdir(parents=True, exist_ok=True)
        (overlay / ".deleted").symlink_to(outside / ".deleted")
        return paths

    monkeypatch.setattr(routes, "_doc_paths", check_then_swap)

    assert client.delete("/knowledge/builtin/strategy/shipped_doc.md").status_code == 400
    assert not list((outside / ".deleted").rglob("*.deleted")), "no tombstone written through the symlink"


# ---- round 1 review of #39

def test_a_hardlink_to_another_file_is_refused_and_the_other_file_is_not_truncated(tmp_path: Path) -> None:
    ov, victim = tmp_path / "ov", tmp_path / "victim.db"
    victim.write_text("precious", encoding="utf-8")
    (ov / "a").mkdir(parents=True)
    os.link(victim, ov / "a" / "x.md")
    with pytest.raises(overlay_fs.UnsafeOverlayPath):
        overlay_fs.write_text(ov, Path("a/x.md"), "clobber")
    assert victim.read_text(encoding="utf-8") == "precious"


def test_a_fifo_at_the_path_is_refused_without_blocking(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    os.mkfifo(tmp_path / "a" / "x.md")
    with pytest.raises(overlay_fs.UnsafeOverlayPath):
        overlay_fs.write_text(tmp_path, Path("a/x.md"), "x")


def test_two_writers_creating_the_same_new_directory_both_succeed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_mkdir = os.mkdir

    def racing_mkdir(name: Any, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        real_mkdir(name, mode, dir_fd=dir_fd)  # the other writer got there first...
        raise FileExistsError(errno.EEXIST, "exists")  # ...so ours reports it

    monkeypatch.setattr(overlay_fs.os, "mkdir", racing_mkdir)
    overlay_fs.write_text(tmp_path, Path("newdomain/x.md"), "ok")
    assert (tmp_path / "newdomain" / "x.md").read_text(encoding="utf-8") == "ok"


def test_an_unwritable_directory_is_an_ordinary_oserror_not_a_path_escape(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a").chmod(0o500)
    try:
        if os.access(tmp_path / "a", os.W_OK):  # running as root: permissions don't bite
            pytest.skip("directory permissions are not enforced for this user")
        with pytest.raises(PermissionError):
            overlay_fs.write_text(tmp_path, Path("a/x.md"), "x")
    finally:
        (tmp_path / "a").chmod(0o755)


def test_file_size_reads_plain_files_only(tmp_path: Path) -> None:
    (tmp_path / "x.md").write_bytes(b"12345")
    (tmp_path / "link.md").symlink_to(tmp_path / "x.md")
    assert overlay_fs.file_size(tmp_path, Path("x.md")) == 5
    assert overlay_fs.file_size(tmp_path, Path("link.md")) == 0
    assert overlay_fs.file_size(tmp_path, Path("missing.md")) == 0


def test_a_failed_tombstone_cleanup_does_not_leave_a_half_created_doc(env: dict[str, Any]) -> None:
    """The write succeeded but removing the tombstone was refused: no file, no 'exists' trap."""
    client, overlay = env["client"], env["overlay"]
    client.delete("/knowledge/builtin/strategy/shipped_doc.md")  # tombstone in <overlay>/.deleted/strategy/
    tomb_dir = overlay / ".deleted" / "strategy"
    tomb_dir.chmod(0o500)  # the tombstone can't be removed
    try:
        if os.access(tomb_dir, os.W_OK):
            pytest.skip("directory permissions are not enforced for this user")
        res = _post(client, "shipped_doc.md", "x" * 50)
        assert res.status_code == 500
        assert not (overlay / "strategy" / "shipped_doc.md").exists(), "the doc written a moment ago is rolled back"
        assert client.post("/knowledge/builtin", json={"domain": "strategy", "filename": "shipped_doc.md", "content": "y"}).status_code != 409
    finally:
        tomb_dir.chmod(0o755)


def test_a_failed_delete_leaves_the_doc_served_with_its_rows(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api.routes import knowledge as routes

    client, store, overlay = env["client"], env["store"], env["overlay"]
    src = str(env["tmp"] / "image" / "builtin" / "strategy" / "shipped_doc.md")
    rows = len(store._get_or_create_collection(ChromaDBStore.BUILTIN_COLLECTION).get(where={"source": src}, include=[])["ids"])
    assert rows > 0
    real = routes._doc_paths

    def swap(*a: Any, **k: Any) -> Any:
        paths = real(*a, **k)
        overlay.mkdir(parents=True, exist_ok=True)
        (overlay / ".deleted").symlink_to(env["tmp"])
        return paths

    monkeypatch.setattr(routes, "_doc_paths", swap)
    assert client.delete("/knowledge/builtin/strategy/shipped_doc.md").status_code == 400
    monkeypatch.setattr(routes, "_doc_paths", real)

    after = len(store._get_or_create_collection(ChromaDBStore.BUILTIN_COLLECTION).get(where={"source": src}, include=[])["ids"])
    assert after == rows, "rows survive a delete that failed"


def test_an_overlay_already_over_a_lowered_budget_still_accepts_a_shrinking_edit(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client = env["client"]
    _post(client, "one.md", "a" * 800)
    monkeypatch.setenv("BUILTIN_OVERLAY_MAX_BYTES", "500")
    assert client.put("/knowledge/builtin/strategy/one.md", json={"domain": "strategy", "filename": "one.md", "content": "b" * 300}).status_code == 200
    assert client.put("/knowledge/builtin/strategy/one.md", json={"domain": "strategy", "filename": "one.md", "content": "b" * 600}).status_code == 413


def test_a_full_volume_is_reported_as_507_not_as_a_path_escape(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def full(*a: Any, **k: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(overlay_fs, "write_text", full)
    assert _post(env["client"], "x.md", "hello").status_code == 507


# ---- round 1 security review of #39: rel validation, atomic replace, body limit

@pytest.mark.parametrize("bad", ["../outside/evil.md", "a/../../evil.md", "/etc/evil.md", ""])
def test_a_relative_path_that_could_leave_the_root_is_refused(tmp_path: Path, bad: str) -> None:
    ov = tmp_path / "ov"
    with pytest.raises(overlay_fs.UnsafeOverlayPath):
        overlay_fs.write_text(ov, Path(bad), "x")
    with pytest.raises(overlay_fs.UnsafeOverlayPath):
        overlay_fs.touch(ov, Path(bad))
    with pytest.raises(overlay_fs.UnsafeOverlayPath):
        overlay_fs.unlink(ov, Path(bad))
    assert not (tmp_path / "outside").exists() and not (tmp_path / "evil.md").exists()


def test_a_write_that_fails_part_way_leaves_the_previous_document_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    overlay_fs.write_text(tmp_path, Path("a/x.md"), "the old document")

    def full(*_a: Any, **_k: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(overlay_fs.os, "rename", full)
    with pytest.raises(OSError):
        overlay_fs.write_text(tmp_path, Path("a/x.md"), "the new document")

    assert (tmp_path / "a" / "x.md").read_text(encoding="utf-8") == "the old document"
    assert [p.name for p in (tmp_path / "a").iterdir()] == ["x.md"], "no temp file left behind"


class _Recorder:
    def __init__(self) -> None:
        self.called = False
        self.read = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.called = True
        while True:
            msg = await receive()
            self.read += len(msg.get("body", b""))
            if not msg.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run_asgi(app: Any, *, method: str = "POST", path: str = "/knowledge/builtin", headers: list[tuple[bytes, bytes]] | None = None, chunks: list[bytes]) -> tuple[int, list[Any]]:
    sent: list[Any] = []
    queue = list(chunks)

    async def receive() -> dict[str, Any]:
        body = queue.pop(0) if queue else b""
        return {"type": "http.request", "body": body, "more_body": bool(queue)}

    async def send(message: Any) -> None:
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "headers": headers or []}
    asyncio.run(app(scope, receive, send))
    return next(m["status"] for m in sent if m["type"] == "http.response.start"), sent


def test_a_declared_oversize_body_is_refused_without_reading_it() -> None:
    from openexecutive.api.body_limit import BodyLimitMiddleware

    inner = _Recorder()
    status, _ = _run_asgi(BodyLimitMiddleware(inner, {"/knowledge/builtin": 100}), headers=[(b"content-length", b"101")], chunks=[b"x"])
    assert status == 413 and not inner.called


def test_a_streamed_oversize_body_is_cut_off_and_never_fully_read() -> None:
    from openexecutive.api.body_limit import BodyLimitMiddleware

    inner = _Recorder()
    status, _ = _run_asgi(BodyLimitMiddleware(inner, {"/knowledge/builtin": 100}), chunks=[b"x" * 60, b"x" * 60, b"x" * 1_000_000])
    assert status == 413
    assert inner.read <= 120, "the tail past the limit is never consumed"


def test_a_body_within_the_limit_and_other_routes_pass_through_untouched() -> None:
    from openexecutive.api.body_limit import BodyLimitMiddleware

    app = BodyLimitMiddleware(_Recorder(), {"/knowledge/builtin": 100})
    assert _run_asgi(app, chunks=[b"x" * 100])[0] == 200
    assert _run_asgi(app, path="/chat", chunks=[b"x" * 5000])[0] == 200
    assert _run_asgi(app, method="GET", chunks=[b"x" * 5000])[0] == 200


def test_the_real_app_refuses_an_oversize_knowledge_write_before_the_route_runs(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.knowledge.loader import MAX_KNOWLEDGE_BODY_BYTES

    big = b'{"domain":"strategy","filename":"big.md","content":"' + b"a" * (MAX_KNOWLEDGE_BODY_BYTES + 10) + b'"}'
    res = env["client"].post("/knowledge/builtin", content=big, headers={"content-type": "application/json"})
    assert res.status_code == 413
    assert not (env["overlay"] / "strategy" / "big.md").exists()


# ---- round 2 logic review of #39

def test_a_chunked_oversize_body_gets_a_413_through_the_real_app(env: dict[str, Any]) -> None:
    """No Content-Length: the limit is enforced on the stream, and FastAPI must not
    turn it into its own 400."""
    from openexecutive.knowledge.loader import MAX_KNOWLEDGE_BODY_BYTES

    chunk = b"a" * (MAX_KNOWLEDGE_BODY_BYTES // 2)

    def body() -> Any:
        yield b'{"domain":"strategy","filename":"chunked.md","content":"'
        for _ in range(4):
            yield chunk
        yield b'"}'

    res = env["client"].post("/knowledge/builtin", content=body(), headers={"content-type": "application/json"})

    assert res.status_code == 413
    assert not (env["overlay"] / "strategy" / "chunked.md").exists()


def test_a_maximum_size_document_of_astral_characters_fits_the_body_limit(env: dict[str, Any]) -> None:
    import json

    doc = "\U0001F600" * 300_000  # 300k characters; json.dumps writes each as a 12-byte surrogate pair
    body = json.dumps({"domain": "strategy", "filename": "emoji.md", "content": doc})
    assert len(body) > 3_500_000
    res = env["client"].post("/knowledge/builtin", content=body, headers={"content-type": "application/json"})
    assert res.status_code == 200, res.text


def test_filename_length_is_bounded_so_the_temp_file_name_fits(env: dict[str, Any]) -> None:
    client = env["client"]
    assert _post(client, "a" * 120 + ".md", "x").status_code == 200
    assert _post(client, "a" * 121 + ".md", "x").status_code == 400


def test_a_delete_that_fails_removing_rows_can_simply_be_retried(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = env["client"], env["store"]
    _post(client, "mine.md", "hello world " * 30)
    real = store.delete_documents
    calls = {"n": 0}

    def flaky(*a: Any, **k: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("vector store unavailable")
        real(*a, **k)

    monkeypatch.setattr(store, "delete_documents", flaky)
    with pytest.raises(RuntimeError):
        client.delete("/knowledge/builtin/strategy/mine.md")

    assert (env["overlay"] / "strategy" / "mine.md").exists(), "the doc is still there, so the DELETE is not a 404 next time"
    assert client.delete("/knowledge/builtin/strategy/mine.md").status_code == 200
    assert not (env["overlay"] / "strategy" / "mine.md").exists()


# ---- round 2 security review of #39

def test_a_delete_whose_row_removal_is_silently_swallowed_keeps_the_doc_and_can_be_retried(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """ChromaDBStore.delete_documents swallows every error. The route must notice the
    rows are still there rather than report success and drop the file."""
    client, store, overlay = env["client"], env["store"], env["overlay"]
    _post(client, "mine.md", "hello world " * 30)
    real = store.delete_documents
    monkeypatch.setattr(store, "delete_documents", lambda *a, **k: None)  # a swallowed failure

    assert client.delete("/knowledge/builtin/strategy/mine.md").status_code == 500
    assert (overlay / "strategy" / "mine.md").exists(), "the file stays, so the DELETE can be retried"

    monkeypatch.setattr(store, "delete_documents", real)
    assert client.delete("/knowledge/builtin/strategy/mine.md").status_code == 200
    assert not (overlay / "strategy" / "mine.md").exists()


def test_the_413_carries_cors_headers_so_the_browser_can_show_it(env: dict[str, Any]) -> None:
    from openexecutive.knowledge.loader import MAX_KNOWLEDGE_BODY_BYTES

    big = b'{"content":"' + b"a" * (MAX_KNOWLEDGE_BODY_BYTES + 10) + b'"}'
    res = env["client"].post(
        "/knowledge/builtin", content=big,
        headers={"content-type": "application/json", "origin": "http://localhost:3000"},
    )
    assert res.status_code == 413
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_the_limit_applies_when_the_app_is_served_under_a_path_prefix() -> None:
    from openexecutive.api.body_limit import BodyLimitMiddleware

    inner = _Recorder()
    app = BodyLimitMiddleware(inner, {"/knowledge/builtin": 100})
    sent: list[Any] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"x" * 500, "more_body": False}

    async def send(message: Any) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/knowledge/builtin", "root_path": "/api", "headers": [(b"content-length", b"500")]}
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 413
    assert not inner.called


def test_a_stale_temp_file_left_by_a_killed_writer_is_swept_but_a_fresh_one_is_not(tmp_path: Path) -> None:
    import time

    (tmp_path / "a").mkdir()
    stale, fresh, real = tmp_path / "a" / ".x.md.aaaa.tmp", tmp_path / "a" / ".y.md.bbbb.tmp", tmp_path / "a" / "x.md"
    for f in (stale, fresh, real):
        f.write_text("data", encoding="utf-8")
    old = time.time() - overlay_fs.STALE_TEMP_SECONDS - 60
    os.utime(stale, (old, old))
    os.utime(real, (old, old))

    assert overlay_fs.sweep_stale_temp(tmp_path) == 1

    assert not stale.exists() and fresh.exists() and real.exists()


def test_writing_sweeps_stale_temp_files_so_they_stop_counting_against_the_budget(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    client, overlay = env["client"], env["overlay"]
    monkeypatch.setenv("BUILTIN_OVERLAY_MAX_BYTES", "1000")
    (overlay / "strategy").mkdir(parents=True)
    ghost = overlay / "strategy" / ".gone.md.cccc.tmp"
    ghost.write_bytes(b"x" * 900)  # left behind by a killed process
    old = time.time() - overlay_fs.STALE_TEMP_SECONDS - 60
    os.utime(ghost, (old, old))

    assert _post(client, "one.md", "a" * 600).status_code == 200, "the ghost no longer eats the quota"
    assert not ghost.exists()
