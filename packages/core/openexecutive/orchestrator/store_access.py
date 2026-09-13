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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openexecutive.knowledge.store import ChromaDBStore


def get_shared_store() -> ChromaDBStore:
    """Return the process-wide store set once in ``api/main.py``'s lifespan
    (the same object ``app.state.store`` was initialized to -- NOT
    necessarily the object it holds *now*: a few routes deliberately swap
    ``app.state.store`` for a fresh instance after a destructive vector-store
    operation, e.g. fixture load/reset or a client-slot switch, without
    updating this singleton; see issue #13's follow-up on swap-safety),
    falling back to a fresh construction when it isn't set (e.g. a unit test
    that never ran the FastAPI lifespan).

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
