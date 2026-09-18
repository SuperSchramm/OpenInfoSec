"""Regression tests for issue #27: scripts/stop-dev.sh must work on macOS/BSD
as well as Linux.

The script originally leaned on four Linux/GNU-isms (`grep -P`, `ss`,
`/proc/<pid>/cwd`, and a bare-name `ps -o comm=`), which made `make stop`
silently do nothing -- and even print a false "Stopped." -- on macOS. These
tests source the script (which then only defines its helper functions and
returns) and exercise those helpers against a throwaway listener on an
ephemeral port, so they run on whatever OS the suite runs on and never touch
real dev servers on 8000/3000.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "scripts" / "stop-dev.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32"
    or shutil.which("bash") is None
    or (shutil.which("ss") is None and shutil.which("lsof") is None),
    reason="needs bash plus either ss or lsof (the same requirement as the script itself)",
)


def _wait_listening(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"listener on :{port} never came up")


def _unused_pid() -> int:
    """A pid that doesn't exist right now (not a just-reaped one, which the OS may recycle)."""
    for pid in range(99990, 90000, -1):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            continue  # exists, owned by someone else
    raise RuntimeError("no unused pid found")


# The child binds port 0 itself and reports what it got, so there's no
# pick-a-port-then-hope window for another process to steal it.
_LISTENER_SRC = (
    "import http.server;"
    "s=http.server.HTTPServer(('127.0.0.1',0),http.server.SimpleHTTPRequestHandler);"
    "print(s.server_address[1],flush=True);s.serve_forever()"
)


def _run_helper(func: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Source the script in a fresh bash and call one of its helpers."""
    return subprocess.run(
        ["bash", "-c", 'source "$1"; shift; "$@"', "_", str(SCRIPT), func, *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def listener(tmp_path: Path) -> Iterator[tuple[subprocess.Popen[str], int, Path]]:
    """A python http.server on a self-assigned port, cwd = tmp_path (outside the repo)."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _LISTENER_SRC],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert proc.stdout is not None
        port = int(proc.stdout.readline().strip())
        _wait_listening(port)
        yield proc, port, tmp_path
    finally:
        proc.kill()
        proc.wait()
        if proc.stdout:
            proc.stdout.close()


def test_sourcing_the_script_defines_helpers_and_does_nothing() -> None:
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; echo sourced; type -t pids_listening_on', "_", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "sourced" in result.stdout
    assert "function" in result.stdout
    assert "Stopped." not in result.stdout


def test_pids_listening_on_finds_the_listener(listener) -> None:
    proc, port, _ = listener
    result = _run_helper("pids_listening_on", str(port))
    assert str(proc.pid) in result.stdout.split()


def test_pids_listening_on_is_empty_for_a_bound_but_not_listening_port() -> None:
    # Holding the port bound (never listen()) keeps another process from
    # grabbing it mid-test, without ever showing up as LISTEN.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        assert _run_helper("pids_listening_on", str(port)).stdout.strip() == ""
        assert _run_helper("port_bound", str(port)).returncode != 0


def test_port_bound_tracks_the_listener(listener) -> None:
    proc, port, _ = listener
    assert _run_helper("port_bound", str(port)).returncode == 0
    proc.kill()
    proc.wait()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _run_helper("port_bound", str(port)).returncode == 0:
        time.sleep(0.1)
    assert _run_helper("port_bound", str(port)).returncode != 0


def test_process_cwd_resolves_the_listeners_directory(listener) -> None:
    proc, _, cwd = listener
    result = _run_helper("process_cwd", str(proc.pid))
    assert Path(result.stdout.strip()).resolve() == cwd.resolve()


def test_belongs_to_repo_rejects_a_process_running_elsewhere(listener) -> None:
    # tmp_path is outside the checkout -> 1 ("resolved to a different path").
    proc, _, _ = listener
    assert _run_helper("belongs_to_repo", str(proc.pid)).returncode == 1


def test_belongs_to_repo_accepts_a_process_running_inside_the_checkout() -> None:
    # A plain sleeper is enough -- only its cwd matters. (Deliberately not an
    # HTTP server: that would serve the checkout's gitignored company/ data.)
    sleeper = subprocess.Popen(["sleep", "30"], cwd=REPO_ROOT / "packages" / "core")
    try:
        assert _run_helper("belongs_to_repo", str(sleeper.pid)).returncode == 0
    finally:
        sleeper.kill()
        sleeper.wait()


def test_looks_like_our_dev_process_matches_on_basename_not_full_path(listener) -> None:
    # macOS `ps -o comm=` prints the full executable path; only the basename
    # may be compared. sys.executable is often a long venv path.
    proc, _, _ = listener
    assert _run_helper("looks_like_our_dev_process", str(proc.pid)).returncode == 0


def test_looks_like_our_dev_process_rejects_other_binaries() -> None:
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        assert _run_helper("looks_like_our_dev_process", str(sleeper.pid)).returncode == 1
    finally:
        sleeper.kill()
        sleeper.wait()


def test_process_is_gone_for_a_missing_pid_but_not_a_live_one(listener) -> None:
    proc, _, _ = listener
    assert _run_helper("process_is_gone", str(proc.pid)).returncode == 1
    assert _run_helper("process_is_gone", str(_unused_pid())).returncode == 0


def test_process_is_gone_treats_an_unreaped_zombie_as_gone() -> None:
    # The case the helper exists for: a child killed but not yet reaped still
    # passes `kill -0`, and on macOS its `ps comm` becomes "(python3.14)".
    zombie = subprocess.Popen(["true"])  # exits immediately; never wait()ed yet
    try:
        deadline = time.monotonic() + 10
        stat = ""
        while time.monotonic() < deadline:
            stat = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(zombie.pid)], capture_output=True, text=True
            ).stdout.strip()
            if stat.startswith("Z"):
                break
            time.sleep(0.05)
        assert stat.startswith("Z"), f"child never became a zombie (stat={stat!r})"
        os.kill(zombie.pid, 0)  # still "exists" as far as kill -0 is concerned
        assert _run_helper("process_is_gone", str(zombie.pid)).returncode == 0
    finally:
        zombie.wait()


@pytest.mark.skipif(shutil.which("netstat") is None, reason="needs netstat")
def test_port_bound_still_sees_a_listener_when_lsof_and_ss_are_unavailable(
    listener, tmp_path: Path
) -> None:
    # An unprivileged lsof can't see another user's sockets (macOS), so
    # port_bound must fall through to netstat rather than report "free" and
    # let the script print a false "Stopped.". Simulate lsof/ss being unable
    # to help by giving bash a PATH that contains neither.
    _, port, _ = listener
    # netstat can be blind in some process lineages (observed on macOS: children
    # of a uv-managed CPython see no TCP sockets at all). If it can't see our
    # own listener from here, this test can't say anything -- skip, don't fake.
    seen = subprocess.run(["netstat", "-an"], capture_output=True, text=True).stdout
    if not re.search(rf"[.:]{port}\s.*LISTEN", seen):
        pytest.skip("netstat cannot see the test listener in this process context")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("bash", "dirname", "ps", "tr", "grep", "sed", "cut", "sort", "awk", "head", "netstat"):
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; port_bound "$2"', "_", str(SCRIPT), str(port)],
        capture_output=True,
        text=True,
        env={"PATH": str(bindir)},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_script_does_not_use_gnu_only_grep_p() -> None:
    # `grep -P` is what broke macOS in the first place (BSD grep rejects it).
    code_lines = [
        line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")
    ]
    offenders = [line for line in code_lines if re.search(r"\bgrep\s+-\w*P", line)]
    assert not offenders, f"GNU-only grep -P reintroduced: {offenders}"


# --- netstat detector, offline: a fake `netstat` prints canned output, and the
# PATH contains neither ss nor lsof, so port_bound/netstat_is_blind can only
# be answering from netstat. Deterministic on any OS, unlike a real listener
# (netstat can be blind in some process lineages -- see the script).

_MACOS_NETSTAT = """\
Active Internet connections (including servers)
Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)
tcp4       0      0  127.0.0.1.8000         *.*                    LISTEN
tcp6       0      0  ::1.3000               *.*                    LISTEN
tcp4       0      0  10.0.0.5.51234         10.0.0.9.8000          ESTABLISHED
tcp4       0      0  10.0.0.5.51240         10.0.0.9.13000         TIME_WAIT
tcp4       0      0  *.22                   *.*                    LISTEN
"""

_LINUX_NETSTAT = """\
Active Internet connections (servers and established)
Proto Recv-Q Send-Q Local Address           Foreign Address         State
tcp        0      0 0.0.0.0:8000            0.0.0.0:*               LISTEN
tcp6       0      0 :::3000                 :::*                    LISTEN
tcp        0      0 10.0.0.5:51234          10.0.0.9:8000           ESTABLISHED
tcp        0      0 0.0.0.0:13000           0.0.0.0:*               LISTEN
udp        0      0 0.0.0.0:5000            0.0.0.0:*
unix  2      [ ACC ]     STREAM     LISTENING     12345    /tmp/x.3000
"""


_ONLY_13000_LISTENER = """\
Proto Recv-Q Send-Q Local Address           Foreign Address         State
tcp        0      0 0.0.0.0:13000           0.0.0.0:*               LISTEN
"""


def _run_with_fake_netstat(tmp_path: Path, netstat_output: str, call: str) -> subprocess.CompletedProcess[str]:
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    for tool in ("bash", "dirname", "ps", "tr", "grep", "sed", "cut", "sort", "awk", "head", "cat"):
        found = shutil.which(tool)
        if found and not (bindir / tool).exists():
            (bindir / tool).symlink_to(found)
    fake = bindir / "netstat"
    fake.write_text(f"#!/bin/sh\ncat <<'EOF'\n{netstat_output}EOF\n")
    fake.chmod(0o755)
    return subprocess.run(
        ["bash", "-c", f'source "$1"; {call}', "_", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": str(bindir)},
        timeout=30,
    )


@pytest.mark.parametrize(
    ("sample", "port", "bound"),
    [
        (_MACOS_NETSTAT, 8000, True),    # 127.0.0.1.8000 LISTEN (and an ESTABLISHED row naming .8000 as *foreign*)
        (_MACOS_NETSTAT, 3000, True),    # IPv6 ::1.3000
        (_MACOS_NETSTAT, 22, True),      # *.22 -- another user's sshd, the case lsof can't see
        (_MACOS_NETSTAT, 13000, False),  # TIME_WAIT row only, never a listener
        (_MACOS_NETSTAT, 51234, False),  # a client port on an ESTABLISHED row
        (_LINUX_NETSTAT, 8000, True),
        (_LINUX_NETSTAT, 3000, True),    # :::3000 -- and the unix-socket row with ".3000" must not matter
        (_LINUX_NETSTAT, 30, False),     # no partial-port matches
        (_LINUX_NETSTAT, 5000, False),   # udp is not a TCP listener
        (_ONLY_13000_LISTENER, 3000, False),  # 3000 must not match as the tail of 13000
        (_ONLY_13000_LISTENER, 13000, True),
    ],
)
def test_port_bound_netstat_parsing(tmp_path: Path, sample: str, port: int, bound: bool) -> None:
    result = _run_with_fake_netstat(tmp_path, sample, f"port_bound {port}")
    assert (result.returncode == 0) is bound, result.stderr


def test_netstat_is_blind_when_it_reports_no_tcp_rows(tmp_path: Path) -> None:
    only_unix = "Active LOCAL (UNIX) domain sockets\nAddress Type Recv-Q\n"
    assert _run_with_fake_netstat(tmp_path, only_unix, "netstat_is_blind").returncode == 0
    assert _run_with_fake_netstat(tmp_path, _MACOS_NETSTAT, "netstat_is_blind").returncode == 1


def test_netstat_is_not_blind_when_netstat_is_absent(tmp_path: Path) -> None:
    bindir = tmp_path / "bare"
    bindir.mkdir()
    for tool in ("bash", "dirname", "ps", "tr"):
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; netstat_is_blind', "_", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": str(bindir)},
        timeout=30,
    )
    assert result.returncode == 1  # absent != blind
