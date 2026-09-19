"""Symlink-safe file operations inside the knowledge overlay (issue #39).

The overlay sits on the persistent volume next to the vector store, a lower trust
boundary than the read-only image. The routes check ``realpath`` containment up
front (``api/routes/knowledge.py::_doc_paths``), but a check followed by a
separate write leaves a window in which a path component can be swapped for a
symlink. These helpers close it: the overlay root is resolved once, then every
component is opened relative to its parent's file descriptor with
``O_NOFOLLOW``, so a symlink anywhere below the root is refused rather than
followed. Platforms without ``O_NOFOLLOW`` / ``dir_fd`` support (Windows) fall
back to plain ``Path`` operations, which keep only the up-front check.
"""
from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
import time
from pathlib import Path

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_SAFE = bool(_NOFOLLOW and _DIRECTORY) and all(
    fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.unlink, os.rename, os.stat)
)


class UnsafeOverlayPath(OSError):
    """A symlink, hardlink, special file or non-directory sits where the overlay expects a plain directory or file."""


# errno values meaning "a symlink / non-directory is in the way", as opposed to a
# full, read-only or unwritable volume (which must stay ordinary OSErrors so the
# caller can tell a security refusal from an operational failure).
_UNSAFE_ERRNOS = {errno.ELOOP, errno.ENOTDIR, errno.EMLINK}
DIR_MODE = 0o755
FILE_MODE = 0o644


def _open_dir(name: str, parent_fd: int | None, path: str | None = None) -> int:
    flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW
    try:
        return os.open(name if parent_fd is not None else path or name, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in _UNSAFE_ERRNOS:
            raise UnsafeOverlayPath(f"unsafe overlay component: {name!r}") from exc
        raise


def _open_parent(root: Path, parts: tuple[str, ...], create: bool) -> int | None:
    """An fd for the directory that holds ``parts[-1]`` (never following symlinks).

    None when a parent is missing and ``create`` is false.
    """
    fd = os.open(os.path.realpath(root), os.O_RDONLY | _DIRECTORY)
    try:
        for name in parts[:-1]:
            try:
                nxt = _open_dir(name, fd)
            except FileNotFoundError:
                if not create:
                    os.close(fd)
                    return None
                with contextlib.suppress(FileExistsError):  # a concurrent writer made it first
                    os.mkdir(name, DIR_MODE, dir_fd=fd)
                nxt = _open_dir(name, fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        raise


def _open_marker(parent_fd: int, name: str) -> int:
    """Open (creating if needed) a plain single-link regular file for a marker, refusing anything else.

    ``O_NOFOLLOW`` stops symlinks; a hardlink or a FIFO planted at the path is
    caught by the ``fstat`` below. ``O_NONBLOCK`` keeps a FIFO from blocking the
    open forever.
    """
    try:
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | _NOFOLLOW | os.O_NONBLOCK, FILE_MODE, dir_fd=parent_fd,
        )
    except OSError as exc:
        if exc.errno in _UNSAFE_ERRNOS or exc.errno == errno.ENXIO:
            raise UnsafeOverlayPath(f"unsafe overlay file: {name!r}") from exc
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise UnsafeOverlayPath(f"unsafe overlay file (not a plain single-link file): {name!r}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _replace_file(parent_fd: int, name: str, content: str) -> None:
    """Atomically replace ``name`` with ``content``: write a temp file beside it, then rename.

    A failure part-way (a full volume, say) leaves the previous file untouched
    instead of truncated. The rename replaces the directory entry rather than
    writing through it, so a hardlink or symlink planted at ``name`` is never
    written to; the ``lstat`` only turns that into a clear refusal.
    """
    try:
        existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
            raise UnsafeOverlayPath(f"unsafe overlay file (not a plain single-link file): {name!r}")
    tmp = f".{name}.{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, FILE_MODE, dir_fd=parent_fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.rename(tmp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=parent_fd)
        raise


def _check_rel(rel: Path) -> None:
    """Only a plain relative path may enter the overlay API: no absolute path, no
    ``..``/``.``/empty component. ``..`` is not a symlink, so ``O_NOFOLLOW`` alone
    would happily walk out of the root, and POSIX ignores ``dir_fd`` for an
    absolute path."""
    if rel.is_absolute() or not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
        raise UnsafeOverlayPath(f"not a plain relative overlay path: {str(rel)!r}")


def _require(fd: int | None) -> int:
    if fd is None:  # only reachable if create=True stopped creating: a programming error
        raise RuntimeError("overlay parent directory was not created")
    return fd


def _create(root: Path, rel: Path, content: str | None) -> None:
    """Create ``root/rel`` (parents too) symlink-safely; write ``content`` (truncating) unless it is None."""
    _check_rel(rel)
    root.mkdir(parents=True, exist_ok=True)
    if not _SAFE:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8") if content is not None else target.touch()
        return
    parent = _require(_open_parent(root, rel.parts, create=True))
    try:
        if content is None:
            os.close(_open_marker(parent, rel.parts[-1]))
        else:
            _replace_file(parent, rel.parts[-1], content)
    finally:
        os.close(parent)


def write_text(root: Path, rel: Path, content: str) -> None:
    """Create or replace ``root/rel`` without following any symlink below ``root``."""
    _create(root, rel, content)


def touch(root: Path, rel: Path) -> None:
    """Create an empty marker file at ``root/rel`` (parents created), symlink-safe."""
    _create(root, rel, None)


def unlink(root: Path, rel: Path) -> None:
    """Remove ``root/rel`` if present, without following symlinks in its parents."""
    _check_rel(rel)
    if not root.is_dir():
        return
    if not _SAFE:
        (root / rel).unlink(missing_ok=True)
        return
    parent = _open_parent(root, rel.parts, create=False)
    if parent is None:
        return
    try:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(rel.parts[-1], dir_fd=parent)
    finally:
        os.close(parent)


def file_size(root: Path, rel: Path) -> int:
    """Size of the plain file at ``root/rel`` (0 if absent, a symlink or not a regular file)."""
    try:
        st = os.lstat(root / rel)
    except OSError:
        return 0
    return st.st_size if stat.S_ISREG(st.st_mode) else 0


def total_bytes(root: Path) -> int:
    """Bytes held by regular files under ``root`` (symlinks are not followed or counted)."""
    total = 0
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            with contextlib.suppress(OSError):
                st = os.lstat(os.path.join(dirpath, name))
                if not os.path.islink(os.path.join(dirpath, name)):
                    total += st.st_size
    return total


STALE_TEMP_SECONDS = 3600


def sweep_stale_temp(root: Path) -> int:
    """Delete temp files (``.<name>.<hex>.tmp``) that a killed process left behind.

    ``_replace_file`` cleans up after every in-process error, but a SIGKILL or an
    OOM between creating the temp file and renaming it leaves one, and it would
    otherwise count against the byte budget forever while staying invisible to the
    ``*.md`` walks. Only files older than ``STALE_TEMP_SECONDS`` go, so a write in
    flight is never touched. Returns how many were removed.
    """
    removed = 0
    cutoff = time.time() - STALE_TEMP_SECONDS
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            if name.startswith(".") and name.endswith(".tmp"):
                full = os.path.join(dirpath, name)
                with contextlib.suppress(OSError):
                    st = os.lstat(full)
                    if stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff:
                        os.unlink(full)
                        removed += 1
    return removed
