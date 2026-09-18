"""Unit tests for the shared attachments module.

All HTTP calls and filesystem operations are mocked — no network or disk I/O.
"""
from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openexecutive.integrations.attachments import (
    _MAX_EXTRACTED_CHARS,
    AttachmentItem,
    build_attachment_output,
    download_bytes,
    process_attachments,
)

# --------------------------------------------------------------------------- #
# download_bytes
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_download_bytes_returns_content():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {}
    mock_resp.content = b"hello world"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        data = await download_bytes("https://example.com/file.pdf")

    assert data == b"hello world"


@pytest.mark.asyncio
async def test_download_bytes_raises_on_content_length_exceeded():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {"content-length": str(30 * 1024 * 1024)}  # 30 MB > 20 MB limit
    mock_resp.content = b"x"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="too large"):
            await download_bytes("https://example.com/big.pdf")


@pytest.mark.asyncio
async def test_download_bytes_raises_when_actual_content_exceeds_limit():
    """Content-Length header absent but actual payload is oversized."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {}
    mock_resp.content = b"x" * (21 * 1024 * 1024)  # 21 MB

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="too large"):
            await download_bytes("https://example.com/big.pdf")


# --------------------------------------------------------------------------- #
# _schedule_ingest
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_schedule_ingest_uses_shared_store_not_a_fresh_construction():
    """Regression test for issue #18: the background ingest task must reuse
    the process-wide store via get_shared_store() instead of constructing a
    fresh ChromaDBStore() (which defaults to a cwd-relative ./chroma_db
    instead of settings.vector_store_path).
    """
    from openexecutive.integrations import attachments

    fake_store = MagicMock()

    with (
        patch(
            "openexecutive.orchestrator.store_access.get_shared_store",
            return_value=fake_store,
        ) as mock_get_store,
        patch(
            "openexecutive.knowledge.loader.ingest_file",
            AsyncMock(return_value=3),
        ) as mock_ingest,
    ):
        attachments._schedule_ingest(b"hello world", "notes.txt")
        pending = list(attachments._ingest_tasks)
        assert pending, "expected a background ingest task to be scheduled"
        await asyncio.gather(*pending)

    mock_get_store.assert_called_once()
    mock_ingest.assert_awaited_once()
    assert mock_ingest.await_args.args[1] is fake_store


@pytest.mark.asyncio
async def test_schedule_ingest_passes_real_filename_as_display_name():
    """Regression test for issue #22: ingest_file must be called with
    display_name=<real filename>, not left to fall back to the throwaway
    tempfile.NamedTemporaryFile path it reads from (which is deleted
    immediately after) — otherwise chunk metadata points at a path that
    no longer exists instead of something attributable and purgeable."""
    from openexecutive.integrations import attachments

    fake_store = MagicMock()

    with (
        patch(
            "openexecutive.orchestrator.store_access.get_shared_store",
            return_value=fake_store,
        ),
        patch(
            "openexecutive.knowledge.loader.ingest_file",
            AsyncMock(return_value=3),
        ) as mock_ingest,
    ):
        attachments._schedule_ingest(b"hello world", "quarterly-report.pdf")
        pending = list(attachments._ingest_tasks)
        assert pending, "expected a background ingest task to be scheduled"
        await asyncio.gather(*pending)

    mock_ingest.assert_awaited_once()
    assert mock_ingest.await_args.kwargs["display_name"] == "quarterly-report.pdf"


@pytest.mark.asyncio
async def test_schedule_ingest_uses_attachment_collection_not_company():
    """Regression test for issue #25 step 1: attachment ingest must target
    ChromaDBStore.ATTACHMENT_COLLECTION, not the default COMPANY_COLLECTION
    — otherwise attachment content (sent by any rostered/authorized user,
    not curated by an admin) shares a collection with /documents uploads
    and gets rendered under the same "curated company documents" trust
    heading in retriever.py."""
    from openexecutive.integrations import attachments
    from openexecutive.knowledge.store import ChromaDBStore

    fake_store = MagicMock()

    with (
        patch(
            "openexecutive.orchestrator.store_access.get_shared_store",
            return_value=fake_store,
        ),
        patch(
            "openexecutive.knowledge.loader.ingest_file",
            AsyncMock(return_value=3),
        ) as mock_ingest,
    ):
        attachments._schedule_ingest(b"hello world", "notes.txt")
        pending = list(attachments._ingest_tasks)
        assert pending, "expected a background ingest task to be scheduled"
        await asyncio.gather(*pending)

    mock_ingest.assert_awaited_once()
    assert (
        mock_ingest.await_args.kwargs["collection"] == ChromaDBStore.ATTACHMENT_COLLECTION
    )


@pytest.mark.asyncio
async def test_schedule_ingest_passes_generation_to_ingest_file():
    """Regression test for issue #26: _schedule_ingest must forward
    get_store_generation()'s value to ingest_file as expected_generation --
    not omit it. This only proves the value is threaded through the kwarg;
    it does not prove *when* it was captured (mocking get_store_generation
    to a constant can't distinguish an early vs. late capture point -- see
    test_schedule_ingest_generation_captured_before_task_is_scheduled below
    for a test that actually exercises the timing)."""
    from openexecutive.integrations import attachments

    fake_store = MagicMock()

    with (
        patch(
            "openexecutive.orchestrator.store_access.get_shared_store",
            return_value=fake_store,
        ),
        patch(
            "openexecutive.orchestrator.store_access.get_store_generation",
            return_value=42,
        ),
        patch(
            "openexecutive.knowledge.loader.ingest_file",
            AsyncMock(return_value=3),
        ) as mock_ingest,
    ):
        attachments._schedule_ingest(b"hello world", "notes.txt")
        pending = list(attachments._ingest_tasks)
        assert pending, "expected a background ingest task to be scheduled"
        await asyncio.gather(*pending)

    mock_ingest.assert_awaited_once()
    assert mock_ingest.await_args.kwargs["expected_generation"] == 42


@pytest.mark.asyncio
async def test_schedule_ingest_keeps_the_legacy_chunking_for_attachments():
    """Issue #31: company docs moved to 120-word chunks (~4.6x more embedding
    work, synchronous on the event loop). Attachments come from any rostered
    sender, so they must keep the old 512/50 chunking -- pin the kwargs that
    actually reach ingest_file, not a source string."""
    from openexecutive.integrations import attachments
    from openexecutive.knowledge.loader import ATTACHMENT_CHUNK_OVERLAP, ATTACHMENT_CHUNK_WORDS

    with (
        patch("openexecutive.orchestrator.store_access.get_shared_store", return_value=MagicMock()),
        patch("openexecutive.knowledge.loader.ingest_file", AsyncMock(return_value=3)) as mock_ingest,
    ):
        attachments._schedule_ingest(b"hello world", "notes.txt")
        pending = list(attachments._ingest_tasks)
        assert pending, "expected a background ingest task to be scheduled"
        await asyncio.gather(*pending)

    mock_ingest.assert_awaited_once()
    assert (ATTACHMENT_CHUNK_WORDS, ATTACHMENT_CHUNK_OVERLAP) == (512, 50)
    assert mock_ingest.await_args.kwargs["chunk_words"] == ATTACHMENT_CHUNK_WORDS
    assert mock_ingest.await_args.kwargs["chunk_overlap"] == ATTACHMENT_CHUNK_OVERLAP


@pytest.mark.asyncio
async def test_schedule_ingest_generation_captured_before_task_is_scheduled():
    """Round-2 security review, issue #26: expected_generation must be
    captured in _schedule_ingest's own synchronous body, before
    loop.create_task(_run()) queues the background task -- not inside
    _run() once it starts running.

    Why this matters: ingest_file has no `await` anywhere in it (text
    extraction is CPU-bound sync work), so once the scheduled task gets a
    turn on the event loop it runs extraction, the generation check, and
    the write as one atomic, non-interleaved step. The only real window in
    which a company switch or admin purge can run and bump the generation
    is *between* _schedule_ingest() returning and that task's first turn --
    exactly the window this test occupies, by bumping the generation right
    after _schedule_ingest() returns and before yielding to let the task
    run. Uses the real ingest_file (not a mock) against a FakeStore so the
    assertion exercises the actual check-and-skip path, not just kwarg
    threading -- a capture point moved into _run() would observe the bump
    that already happened and (wrongly) match itself, letting this content
    through; a mocked ingest_file couldn't tell the difference either way."""
    from openexecutive.integrations import attachments
    from openexecutive.orchestrator.store_access import (
        _reset_store_generation_for_tests,
        bump_store_generation,
    )

    class _FakeStore:
        def __init__(self) -> None:
            self.collections: dict[str, list[dict]] = {}

        def add_documents(self, texts, metadatas, ids, collection):
            col = self.collections.setdefault(collection, [])
            for t, m, i in zip(texts, metadatas, ids, strict=True):
                col.append({"id": i, "text": t, "metadata": m})

    _reset_store_generation_for_tests()
    fake_store = _FakeStore()

    try:
        with patch(
            "openexecutive.orchestrator.store_access.get_shared_store",
            return_value=fake_store,
        ):
            attachments._schedule_ingest(b"some real attachment content", "notes.txt")
            # Simulate a company switch / admin purge landing in the gap
            # between scheduling and the task's first (only) turn.
            bump_store_generation()
            pending = list(attachments._ingest_tasks)
            assert pending, "expected a background ingest task to be scheduled"
            await asyncio.gather(*pending)
    finally:
        _reset_store_generation_for_tests()

    assert fake_store.collections == {}, (
        "a generation captured before scheduling must see the mid-window "
        "bump and skip the write -- if this fires, the capture point "
        "regressed back inside _run()"
    )


# --------------------------------------------------------------------------- #
# build_attachment_output — image routing
# --------------------------------------------------------------------------- #

def test_build_attachment_output_png_returns_image_block():
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20  # fake PNG header
    extra_text, image_blocks = build_attachment_output(
        "chart.png", data, "image/png", authorized=True
    )

    assert extra_text == ""
    assert len(image_blocks) == 1
    block = image_blocks[0]
    assert block["type"] == "image"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/png"
    assert block["source"]["data"] == base64.standard_b64encode(data).decode()


def test_build_attachment_output_jpeg_normalises_jpg_mime():
    """'image/jpg' (non-standard) must be normalised to 'image/jpeg'."""
    data = b"\xff\xd8\xff"  # JPEG magic bytes
    extra_text, image_blocks = build_attachment_output(
        "photo.jpg", data, "image/jpg", authorized=True
    )

    assert extra_text == ""
    assert image_blocks[0]["source"]["media_type"] == "image/jpeg"


def test_build_attachment_output_image_no_content_type_infers_from_suffix():
    data = b"GIF89a"
    extra_text, image_blocks = build_attachment_output(
        "anim.gif", data, "", authorized=True
    )

    assert extra_text == ""
    assert image_blocks[0]["source"]["media_type"] == "image/gif"


# --------------------------------------------------------------------------- #
# build_attachment_output — text document routing
# --------------------------------------------------------------------------- #

def test_build_attachment_output_pdf_extracts_text_and_schedules_ingest_when_authorized():
    extracted = "Quarterly revenue grew 23%."
    with (
        patch(
            "openexecutive.integrations.attachments._extract_text",
            return_value=extracted,
        ),
        patch("openexecutive.integrations.attachments._schedule_ingest") as mock_ingest,
    ):
        extra_text, image_blocks = build_attachment_output(
            "report.pdf", b"%PDF-fake", "application/pdf", authorized=True
        )

    assert image_blocks == []
    assert "[Attached: report.pdf]" in extra_text
    assert "Quarterly revenue grew 23%." in extra_text
    mock_ingest.assert_called_once()


def test_build_attachment_output_pdf_extracts_text_but_skips_ingest_when_unauthorized():
    """Regression test for issue #21: an unauthorized caller (e.g. Discord's
    on_message before the roster check clears) still gets extracted text
    back for its own throwaway reply, but MUST NOT trigger the shared
    ChromaDB ingest — that's the actual security-relevant side effect."""
    extracted = "Quarterly revenue grew 23%."
    with (
        patch(
            "openexecutive.integrations.attachments._extract_text",
            return_value=extracted,
        ),
        patch("openexecutive.integrations.attachments._schedule_ingest") as mock_ingest,
    ):
        extra_text, image_blocks = build_attachment_output(
            "report.pdf", b"%PDF-fake", "application/pdf", authorized=False
        )

    assert image_blocks == []
    assert "[Attached: report.pdf]" in extra_text
    assert "Quarterly revenue grew 23%." in extra_text
    mock_ingest.assert_not_called()


def test_build_attachment_output_truncates_long_text():
    # 10x the limit so the label overhead is negligible relative to the total.
    long_text = "word " * (_MAX_EXTRACTED_CHARS * 2)
    with (
        patch(
            "openexecutive.integrations.attachments._extract_text",
            return_value=long_text,
        ),
        patch("openexecutive.integrations.attachments._schedule_ingest"),
    ):
        extra_text, _ = build_attachment_output(
            "doc.txt", b"...", "text/plain", authorized=True
        )

    assert "truncated" in extra_text.lower()
    # Total extra_text is label + capped text; must be much smaller than input.
    assert len(extra_text) < len(long_text) // 2


def test_build_attachment_output_empty_extraction_returns_notice():
    with patch(
        "openexecutive.integrations.attachments._extract_text",
        return_value="   ",
    ):
        extra_text, image_blocks = build_attachment_output(
            "empty.pdf", b"", "application/pdf", authorized=True
        )

    assert image_blocks == []
    assert "could not extract" in extra_text.lower()


# --------------------------------------------------------------------------- #
# build_attachment_output — unsupported type
# --------------------------------------------------------------------------- #

def test_build_attachment_output_unsupported_type_returns_notice():
    extra_text, image_blocks = build_attachment_output(
        "model.xlsx", b"PK...", "application/vnd.ms-excel", authorized=True
    )

    assert image_blocks == []
    assert "unsupported type" in extra_text.lower()
    assert "model.xlsx" in extra_text


# --------------------------------------------------------------------------- #
# process_attachments
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_process_attachments_skips_oversized_item():
    items = [
        AttachmentItem(
            url="https://example.com/big.pdf",
            filename="big.pdf",
            content_type="application/pdf",
            size=25 * 1024 * 1024,  # 25 MB > 20 MB limit
        )
    ]
    extra_text, image_blocks = await process_attachments(items, authorized=True)

    assert "too large" in extra_text.lower() or "skipped" in extra_text.lower()
    assert image_blocks == []


@pytest.mark.asyncio
async def test_process_attachments_skips_failed_download_and_continues():
    """A download error on item 1 must not stop item 2 from processing."""
    items = [
        AttachmentItem(url="https://fail.example.com/a.pdf", filename="a.pdf", content_type="application/pdf"),
        AttachmentItem(url="https://ok.example.com/b.png", filename="b.png", content_type="image/png"),
    ]

    async def _fake_download(url: str, headers=None, max_bytes=None):
        if "fail" in url:
            raise ConnectionError("network down")
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 20

    with patch("openexecutive.integrations.attachments.download_bytes", side_effect=_fake_download):
        extra_text, image_blocks = await process_attachments(items, authorized=True)

    # Item 1 failed — note in text
    assert "a.pdf" in extra_text
    # Item 2 succeeded — got an image block
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/png"


@pytest.mark.asyncio
async def test_process_attachments_authorized_false_never_schedules_ingest():
    """Regression test for issue #21: authorized=False must propagate to
    EVERY item, not just the first — a multi-attachment message from an
    unrostered Discord sender must not ingest any of its documents."""
    items = [
        AttachmentItem(url="https://example.com/a.txt", filename="a.txt", content_type="text/plain"),
        AttachmentItem(url="https://example.com/b.pdf", filename="b.pdf", content_type="application/pdf"),
    ]

    async def _fake_download(url: str, headers=None, max_bytes=None):
        return b"content from " + url.encode().split(b"/")[-1]

    with (
        patch("openexecutive.integrations.attachments.download_bytes", side_effect=_fake_download),
        patch(
            "openexecutive.integrations.attachments._extract_text",
            side_effect=lambda data, filename: data.decode(),
        ),
        patch("openexecutive.integrations.attachments._schedule_ingest") as mock_ingest,
    ):
        extra_text, _ = await process_attachments(items, authorized=False)

    assert "a.txt" in extra_text
    assert "b.pdf" in extra_text
    mock_ingest.assert_not_called()


@pytest.mark.asyncio
async def test_process_attachments_concatenates_multiple_texts():
    items = [
        AttachmentItem(url="https://example.com/a.txt", filename="a.txt", content_type="text/plain"),
        AttachmentItem(url="https://example.com/b.txt", filename="b.txt", content_type="text/plain"),
    ]

    async def _fake_download(url: str, headers=None, max_bytes=None):
        return b"content from " + url.encode().split(b"/")[-1]

    with (
        patch("openexecutive.integrations.attachments.download_bytes", side_effect=_fake_download),
        patch(
            "openexecutive.integrations.attachments._extract_text",
            side_effect=lambda data, filename: data.decode(),
        ),
        patch("openexecutive.integrations.attachments._schedule_ingest"),
    ):
        extra_text, image_blocks = await process_attachments(items, authorized=True)

    assert "a.txt" in extra_text
    assert "b.txt" in extra_text
    assert image_blocks == []
