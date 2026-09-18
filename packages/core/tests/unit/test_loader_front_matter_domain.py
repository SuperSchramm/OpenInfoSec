"""Tests for issue #29's loader half: a markdown doc can declare its domain in
front-matter, but ONLY for a caller that opts in (``honor_front_matter=True``,
which just the fixture loader does) and passed no explicit domain.

Fixture docs land in ``company/docs/*.md`` -- no domain segment in the path --
so path inference alone tags every one of them "general". Front-matter lets a
fixture author say what a doc is instead. It is document CONTENT, so it is
never honored anywhere else (uploads, attachments, client-switch re-ingest).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from openexecutive.knowledge import loader as loader_mod
from openexecutive.knowledge.loader import _split_front_matter_domain, ingest_file


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add_documents(self, texts, metadatas, ids, collection):  # noqa: ANN001
        for t, m in zip(texts, metadatas, strict=True):
            self.rows.append({"text": t, "metadata": m})


def _ingest(path: Path, **kwargs: Any) -> tuple[int, _FakeStore]:
    """Ingest as the fixture loader does (opted in) unless a test says otherwise."""
    kwargs.setdefault("honor_front_matter", True)
    store = _FakeStore()
    count = asyncio.run(ingest_file(path, store, **kwargs))  # type: ignore[arg-type]
    return count, store


def _doc(tmp_path: Path, body: str, name: str = "policy.md") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_front_matter_domain_tags_the_chunks_and_is_stripped(tmp_path: Path) -> None:
    path = _doc(tmp_path, "---\ndomain: security\n---\nIncident response plan body text.\n")
    count, store = _ingest(path)

    assert count == 1
    assert store.rows[0]["metadata"]["domain"] == "security"
    assert "domain:" not in store.rows[0]["text"]
    assert store.rows[0]["text"].startswith("Incident response plan body text.")


def test_no_front_matter_still_falls_back_to_general(tmp_path: Path) -> None:
    _, store = _ingest(_doc(tmp_path, "Plain policy text."))
    assert store.rows[0]["metadata"]["domain"] == "general"


def test_explicit_domain_argument_beats_front_matter_and_leaves_text_alone(tmp_path: Path) -> None:
    body = "---\ndomain: security\n---\nBody.\n"
    _, store = _ingest(_doc(tmp_path, body), domain="finance")

    assert store.rows[0]["metadata"]["domain"] == "finance"
    # Front-matter is only interpreted when the caller passed no domain, so the
    # text is exactly what it was before this feature existed.
    assert "domain: security" in store.rows[0]["text"]


def test_sender_controlled_content_cannot_pick_its_own_tag(tmp_path: Path) -> None:
    # The attachment path always passes an explicit domain ("company_docs");
    # a doc claiming "domain: security" must not override it.
    body = "---\ndomain: security\n---\nAttacker wants specialists to trust this.\n"
    _, store = _ingest(_doc(tmp_path, body), domain="company_docs")
    assert store.rows[0]["metadata"]["domain"] == "company_docs"


def test_front_matter_beats_path_inference(tmp_path: Path) -> None:
    (tmp_path / "finance").mkdir()
    path = _doc(tmp_path / "finance", "---\ndomain: legal\n---\nContract terms.\n")
    _, store = _ingest(path)
    assert store.rows[0]["metadata"]["domain"] == "legal"


def test_path_inference_still_works_without_front_matter(tmp_path: Path) -> None:
    (tmp_path / "finance").mkdir()
    _, store = _ingest(_doc(tmp_path / "finance", "Budget notes."))
    assert store.rows[0]["metadata"]["domain"] == "finance"


def test_domain_is_case_and_whitespace_insensitive(tmp_path: Path) -> None:
    _, store = _ingest(_doc(tmp_path, "---\ndomain:  Security \n---\nBody.\n"))
    assert store.rows[0]["metadata"]["domain"] == "security"


@pytest.fixture
def loader_logger(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Assert on the module's logger directly. caplog attaches at the root, but
    api/main.py sets propagate=False on the "openexecutive" logger once any
    earlier test has built the app -- so caplog silently sees nothing in a
    full run (it passes in isolation), which is exactly the kind of ambient
    state a unit test shouldn't depend on."""
    mock = MagicMock()
    monkeypatch.setattr(loader_mod, "logger", mock)
    return mock


def test_unknown_domain_warns_indexes_as_general_and_strips(
    tmp_path: Path, loader_logger: MagicMock
) -> None:
    # A typo must not hide a doc behind a domain no specialist queries.
    _, store = _ingest(_doc(tmp_path, "---\ndomain: secuirty\n---\nBody.\n"))

    assert store.rows[0]["metadata"]["domain"] == "general"
    assert "domain:" not in store.rows[0]["text"]
    loader_logger.warning.assert_called_once()
    assert "secuirty" in str(loader_logger.warning.call_args)


def test_non_string_domain_is_ignored_with_a_warning(
    tmp_path: Path, loader_logger: MagicMock
) -> None:
    _, store = _ingest(_doc(tmp_path, "---\ndomain: [security, legal]\n---\nBody.\n"))
    assert store.rows[0]["metadata"]["domain"] == "general"
    loader_logger.warning.assert_called_once()
    assert "ignoring front-matter domain" in str(loader_logger.warning.call_args)


def test_front_matter_without_a_domain_key_is_indexed_untouched(tmp_path: Path) -> None:
    body = "---\ntitle: Handbook\ntags: [a, b]\n---\nBody text.\n"
    _, store = _ingest(_doc(tmp_path, body))
    assert store.rows[0]["metadata"]["domain"] == "general"
    assert "title: Handbook" in store.rows[0]["text"]


def test_front_matter_only_applies_to_markdown(tmp_path: Path) -> None:
    _, store = _ingest(_doc(tmp_path, "---\ndomain: security\n---\nBody.\n", name="notes.txt"))
    assert store.rows[0]["metadata"]["domain"] == "general"


def test_crlf_front_matter_is_recognised(tmp_path: Path) -> None:
    p = tmp_path / "policy.md"
    p.write_bytes(b"---\r\ndomain: hr\r\n---\r\nPeople policy body.\r\n")
    _, store = _ingest(p)
    assert store.rows[0]["metadata"]["domain"] == "hr"
    assert store.rows[0]["text"].startswith("People policy body.")


def test_a_doc_that_is_only_front_matter_indexes_nothing(tmp_path: Path) -> None:
    count, store = _ingest(_doc(tmp_path, "---\ndomain: security\n---\n"))
    assert count == 0
    assert store.rows == []


def test_malformed_yaml_front_matter_is_left_alone(tmp_path: Path) -> None:
    body = "---\ndomain: [unclosed\n---\nBody.\n"
    _, store = _ingest(_doc(tmp_path, body))
    assert store.rows[0]["metadata"]["domain"] == "general"
    assert "unclosed" in store.rows[0]["text"]


def test_split_helper_ignores_a_horizontal_rule_that_is_not_front_matter() -> None:
    # A "---" rule mid-document, or a doc starting with prose, is not front-matter.
    assert _split_front_matter_domain("Intro\n\n---\ndomain: security\n---\nMore", "x.md") == (
        None,
        "Intro\n\n---\ndomain: security\n---\nMore",
    )


def test_front_matter_is_ignored_unless_the_caller_opts_in(tmp_path: Path) -> None:
    # The default: /documents re-ingest on a client switch, restore-from-backup,
    # etc. must index a doc exactly as before -- its own body never re-tags it.
    body = "---\ndomain: security\n---\nBody.\n"
    _, store = _ingest(_doc(tmp_path, body), honor_front_matter=False)
    assert store.rows[0]["metadata"]["domain"] == "general"
    assert "domain: security" in store.rows[0]["text"]


def test_opt_in_defaults_to_off() -> None:
    import inspect

    assert inspect.signature(ingest_file).parameters["honor_front_matter"].default is False


def test_a_utf8_bom_does_not_defeat_front_matter(tmp_path: Path) -> None:
    # Windows Notepad writes one; read_text(encoding="utf-8") keeps it.
    p = tmp_path / "policy.md"
    p.write_bytes("\ufeff---\ndomain: hr\n---\nPeople policy body.\n".encode())
    _, store = _ingest(p)
    assert store.rows[0]["metadata"]["domain"] == "hr"
    assert store.rows[0]["text"].startswith("People policy body.")


def test_hostile_deeply_nested_front_matter_cannot_abort_ingest(tmp_path: Path) -> None:
    # yaml.safe_load raises RecursionError (not YAMLError) on deep flow nesting.
    # Fixture load / client switch call ingest in a loop AFTER wiping the
    # collection, so one such file must not be able to abort the re-index.
    body = "---\ndomain: " + "[" * 1000 + "\n---\nBody after hostile block.\n"
    count, store = _ingest(_doc(tmp_path, body))
    assert count == 1
    assert store.rows[0]["metadata"]["domain"] == "general"


def test_an_oversized_front_matter_block_is_never_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = MagicMock(side_effect=AssertionError("yaml.safe_load must not see an oversized block"))
    monkeypatch.setattr(loader_mod.yaml, "safe_load", parsed)
    body = "---\ndomain: security\nfiller: " + "x" * 1500 + "\n---\nBody.\n"
    count, store = _ingest(_doc(tmp_path, body))
    assert count == 1
    parsed.assert_not_called()
    assert store.rows[0]["metadata"]["domain"] == "general"


def test_the_warning_logs_a_sanitized_filename(tmp_path: Path, loader_logger: MagicMock) -> None:
    # The filename is uploader-controlled; a newline in it must not forge log lines.
    path = tmp_path / "evil\nFORGED LOG LINE.md"
    path.write_text("---\ndomain: nope\n---\nBody.\n", encoding="utf-8")
    _ingest(path)
    loader_logger.warning.assert_called_once()
    logged_name = loader_logger.warning.call_args.args[2]
    assert "\n" not in logged_name


def test_only_a_fixture_load_opts_in_to_front_matter() -> None:
    """Wiring guard (source-inspection, like this suite's other
    _apply_state_from_source tests -- the function drags in ChromaDB, Honcho
    and an app.state shim). A fixture load may honor author-declared domains;
    restore-from-backup re-indexes the USER's own uploads and must not."""
    import inspect

    from openexecutive.cli import fixture_loader

    assert (
        inspect.signature(fixture_loader._apply_state_from_source)
        .parameters["honor_front_matter"]
        .default
        is False
    )
    # _load_from_dir is the shared body of load_fixture (curated) and
    # load_fixture_any (generated); both go through it.
    assert "honor_front_matter=True" in inspect.getsource(fixture_loader._load_from_dir)
    # (assert on the *opt-in call*, not the bare word, so an explanatory comment
    # in unload_fixture can't break this)
    assert "honor_front_matter=True" not in inspect.getsource(fixture_loader.unload_fixture)


def test_the_hostile_block_really_reaches_the_recursion_path(tmp_path: Path) -> None:
    # Guard against the hostile-input test silently going vacuous (e.g. if the
    # size cap is lowered below its payload): this payload must fit under the
    # cap AND make PyYAML raise something other than YAMLError.
    payload = "domain: " + "[" * 1000
    assert len(payload) <= loader_mod._MAX_FRONT_MATTER_CHARS
    with pytest.raises(RecursionError):
        loader_mod.yaml.safe_load(payload)


def test_an_oversized_block_warns_instead_of_failing_silently(
    tmp_path: Path, loader_logger: MagicMock
) -> None:
    body = "---\ndomain: security\nfiller: " + "x" * 1500 + "\n---\nBody.\n"
    _ingest(_doc(tmp_path, body))
    loader_logger.warning.assert_called_once()
    assert "over" in str(loader_logger.warning.call_args)


def test_malformed_yaml_warns_instead_of_failing_silently(
    tmp_path: Path, loader_logger: MagicMock
) -> None:
    _ingest(_doc(tmp_path, "---\ndomain: [unclosed\n---\nBody.\n"))
    loader_logger.warning.assert_called_once()
    assert "not valid YAML" in str(loader_logger.warning.call_args)
