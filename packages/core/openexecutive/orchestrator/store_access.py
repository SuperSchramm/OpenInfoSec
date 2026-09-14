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

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openexecutive.knowledge.store import ChromaDBStore


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
    """
    from openexecutive.mcp_server import server as mcp_server

    mcp_server.set_store(new_store)
    if app_state is not None and hasattr(app_state, "store"):
        app_state.store = new_store
