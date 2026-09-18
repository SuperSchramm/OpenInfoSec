"""Shared ``ChromaDBStore`` accessor for orchestrator tool handlers.

Tool handlers (``skills_tools``, ``talent_tools``, ``onboarding_tools``,
``workflow_run_tools``, ``research_tools``) are dispatched with no
Request/app access at all -- they can't reach ``app.state.store`` the way a
route handler can. ``get_shared_store()`` gives them the same process-wide
store instead of each constructing its own (see GitHub issue #13: chromadb's
``PersistentClient`` is not safe to construct concurrently against the same
on-disk path, though concurrent queries against one already-built client
are fine).
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openexecutive.knowledge.store import ChromaDBStore


# Swap generation (issue #26). A counter bumped every time the process-wide
# store (or a collection within it) is destructively swapped/wiped -- see
# `publish_swapped_store` below and `ChromaDBStore.delete_attachment_docs`'s
# bump. A caller that's about to start a long-running background write
# (attachments.py's `_schedule_ingest`) captures the generation *before*
# scheduling that work, then re-checks it right before the actual write; a
# mismatch means a company switch (or an admin attachment purge) ran in
# between, and the stale write is skipped rather than landing in whatever
# now lives under that collection name. See `ingest_file`'s docstring for
# why the capture point matters as much as the check point.
# `bump_store_generation()` below takes `_generation_lock`, not a bare
# `+= 1`: every caller today happens to run on this process's
# single-threaded asyncio event loop, so there's no *current*
# concurrent-increment hazard, but nothing enforces that invariant, and
# this codebase's own convention (attachment_retention.py, notion_sync.py,
# external_sources.py, loader.py's own docstring) is to push slow ChromaDB
# calls onto a worker thread via `asyncio.to_thread` -- the moment one of
# this module's callers does that, a bare `+= 1` (load/add/store, not
# atomic even under the GIL) could interleave and lose an increment
# (security review round 2: worked through the actual interleaving and
# confirmed it can only ever cause a spurious skip, never a missed one --
# still worth closing outright rather than leaving as a documented risk).
# A `threading.Lock` (not `asyncio.Lock`, which only excludes other
# coroutines on the SAME thread and does nothing against a real OS thread
# from `asyncio.to_thread`) makes the read-modify-write atomic regardless
# of which primitive a future caller ends up on.
_generation_lock = threading.Lock()
_generation = 0


def get_store_generation() -> int:
    """Current swap generation — see the module comment above `_generation`."""
    return _generation


def bump_store_generation() -> int:
    """Advance and return the swap generation. Called by ``publish_swapped_store``
    below (every company switch) and by ``ChromaDBStore.delete_attachment_docs``
    (the admin manual-purge route, which wipes that collection without a full
    store swap) -- both are events a long-running background ingest must not
    write past. A plain function rather than callers reaching into
    ``_generation`` directly, so the increment stays defined in one place."""
    global _generation
    with _generation_lock:
        _generation += 1
        return _generation


def _reset_store_generation_for_tests() -> None:
    """Test-only: reset the generation counter to 0. Mirrors
    ``mcp_server.server.set_store(None)``'s role in
    ``test_store_singleton_sync.py``'s autouse fixture -- this is
    module-level process state, so tests that care about the counter's
    *absolute* value (rather than just "did it change") need to reset it
    first to stay independent of run order and of other tests in the same
    session that already bumped it."""
    global _generation
    with _generation_lock:
        _generation = 0


def get_shared_store() -> ChromaDBStore:
    """Return the process-wide store: the object set once in ``api/main.py``'s
    lifespan, or the most recent object published via ``publish_swapped_store``
    below (fixture load/unload/reset, a client-slot switch -- see issue #16),
    falling back to a fresh construction when neither has run (e.g. a unit
    test that never ran the FastAPI lifespan).

    Imports stay deferred (function-local) so a test's
    ``monkeypatch.setattr(knowledge_store, "ChromaDBStore", ...)`` still
    takes effect on the fallback path. The check is deliberately
    `is not None`, not `isinstance`: some tests monkeypatch ``ChromaDBStore``
    with a plain lambda, not a class, and `isinstance` against that would
    raise `TypeError` regardless of what the singleton holds.
    """
    from openexecutive.config import get_settings
    from openexecutive.knowledge.store import ChromaDBStore
    from openexecutive.mcp_server.server import get_store

    shared = get_store()
    if shared is not None:
        return shared
    return ChromaDBStore(persist_directory=get_settings().vector_store_path)


def publish_swapped_store(app_state: Any | None, new_store: ChromaDBStore) -> None:
    """Publish a freshly-constructed store after a destructive vector-store
    operation (fixture load/unload/reset, a client-slot switch): refreshes
    the ``mcp_server`` singleton ``get_shared_store()`` reads for tool
    handlers with no Request/app access, and -- when an ``app_state`` is
    available -- ``app_state.store`` too.

    Before issue #16, each of the 4 original swap sites updated only
    ``app_state.store`` directly -- the singleton kept pointing at the
    pre-swap store indefinitely.

    The singleton refresh is unconditional (unlike ``app_state.store``,
    which needs somewhere to write to): every caller of this function is
    already running in-process under the API's lifespan -- there is no
    genuinely standalone caller that lacks both an app_state AND a live
    singleton to refresh. A caller with no ``app_state`` (issue #15's
    follow-up: the scheduler's client-rotation path calls
    ``clients.slots._rebuild_vector_state`` with ``app_state=None``) would
    otherwise leave the singleton stale after its swap even though the
    process-wide store it should refresh is very much live.

    Also bumps the swap generation (issue #26) unconditionally, for the
    same reason the singleton refresh is unconditional -- every caller of
    this function just did a destructive swap that a long-running
    background ingest could otherwise write stale data into. See
    ``get_store_generation``'s module comment above.
    """
    bump_store_generation()

    from openexecutive.mcp_server import server as mcp_server

    mcp_server.set_store(new_store)
    if app_state is not None and hasattr(app_state, "store"):
        app_state.store = new_store
