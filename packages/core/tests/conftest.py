from __future__ import annotations

import os

import pytest

# Required env vars for Settings() — set here so individual test modules
# don't each have to remember. Real values come from .env in dev/prod.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-not-used")
os.environ.setdefault("EXEC_EMAIL_ADDRESS", "ceo.test@example.com")


@pytest.fixture(autouse=True)
def reset_active_gateway():
    """Ensure the module-level MCP gateway singleton is cleared between tests."""
    from openexecutive.orchestrator.mcp_gateway import set_active_gateway
    set_active_gateway(None)
    yield
    set_active_gateway(None)


@pytest.fixture(autouse=True)
def reset_active_store():
    """Ensure the module-level ChromaDB store singleton is cleared between tests.

    Mirrors reset_active_gateway above. Any test that runs the real FastAPI
    lifespan (``with TestClient(app):``) calls ``mcp_server.set_store()`` and,
    without this reset, leaves that store sitting in the global for every
    later test in the same process -- silently defeating any subsequent
    test's `ChromaDBStore` monkeypatch, since the orchestrator tool-handler
    modules' `_get_store()` helpers check this singleton before ever
    consulting `ChromaDBStore`.
    """
    from openexecutive.mcp_server.server import set_store
    set_store(None)
    yield
    set_store(None)


@pytest.fixture(autouse=True)
def _reset_store_generation():
    """Isolate the process-wide swap-generation counter (issue #26) between
    tests. Mirrors reset_active_gateway/reset_active_store above -- without
    this, a test asserting an absolute generation value would be
    order-dependent on every other test in the session that also calls
    publish_swapped_store()/delete_attachment_docs()/bump_store_generation()."""
    from openexecutive.orchestrator import store_access

    store_access._reset_store_generation_for_tests()
    yield
    store_access._reset_store_generation_for_tests()


@pytest.fixture(autouse=True)
def isolate_settings_from_dotenv(monkeypatch: pytest.MonkeyPatch):
    """Keep a developer's repo-root ``.env`` out of every test (issue #28).

    ``Settings`` reads ``env_file=<repo>/.env`` on every ``get_settings()``
    call, so a real dev ``.env`` (a key set to a non-default, e.g.
    ``ENABLE_WEB_SEARCH=false``) silently changed behavior under test -- 4
    tests failed locally that pass in CI, which has no ``.env``. Dropping the
    file makes a local run match CI. Tests that need a specific value set it
    explicitly (``monkeypatch.setenv`` or ``Settings(SOME_KEY=...)``); an
    explicit ``Settings(_env_file=...)`` argument still wins over this.

    Only the FILE is isolated: variables already exported in the shell still
    reach ``Settings`` (CLAUDE.md's ``BACKEND_SHARED_SECRET`` note is the known
    case), so a test that depends on one should set it itself.
    """
    from openexecutive.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    yield


@pytest.fixture(autouse=True)
def isolate_builtin_overlay(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):
    """Point the writable built-in overlay (issue #36) at a per-test temp dir.

    Its default sits next to the vector store, i.e. the repo root in a dev
    checkout, so without this any test that exercises the built-in CRUD routes
    would write into the developer's real ``builtin_custom`` folder.
    """
    monkeypatch.setenv("BUILTIN_OVERLAY_PATH", str(tmp_path_factory.mktemp("builtin_overlay")))
    yield


@pytest.fixture(autouse=True)
def isolate_real_data_paths(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):
    """Pin the vector store and company folder to a per-test temp dir (issue #49).

    ``vector_store_path`` and ``company_profile_path`` default to the repo's real
    ``chroma_db/`` and ``company/``, and everything the fixture/client routes,
    the ``_user_backup`` snapshot and the Honcho workspace file touch hangs off
    them. A test that did not override both used to open the developer's real
    data on every run (and a broken access gate let a destructive route wipe it).
    Tests that need a specific location still set the variable themselves; their
    ``monkeypatch.setenv`` runs after this fixture and wins.
    """
    root = tmp_path_factory.mktemp("real_data_guard")
    (root / "company").mkdir()
    monkeypatch.setenv("VECTOR_STORE_PATH", str(root / "chroma_db"))
    monkeypatch.setenv("COMPANY_PROFILE_PATH", str(root / "company" / "profile.yaml"))
    yield
