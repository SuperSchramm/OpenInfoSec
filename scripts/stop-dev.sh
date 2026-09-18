#!/usr/bin/env bash
# Stops both `make dev` servers (FastAPI:8000, Next.js:3000), including
# reload/worker children that survive a naive pkill (issue #7).
#
# Two mechanisms, each covering the other's blind spot:
#   1. Port-based kill via `ss` (not `lsof` -- in this environment lsof
#      unreliably returns empty for sockets `ss -ltnp` and a raw connect()
#      both confirm are open; ss was reliable in every test here). This
#      kills whoever actually HOLDS the port, including children a
#      name-based pattern can't see: uvicorn --reload's worker is
#      `python -c ...multiprocessing.spawn...` (no "uvicorn" in its
#      cmdline), and Next's actual listener is `next-server (vX)` (no
#      "next dev" in its cmdline).
#   2. Name-based backstop (pgrep -f), for a supervisor that's momentarily
#      not holding the port (e.g. mid-reload-cycle) and could otherwise
#      respawn a fresh worker right after this script exits.
#
# This must run as a standalone script, NOT inlined into a Makefile recipe
# line: `make` runs each recipe line via `sh -c "<literal recipe text>"`,
# so a pkill/pgrep pattern embedded in the recipe line matches that
# invoking shell's own argv and kills it -- aborting the target before
# later lines run. Invoking this file by path keeps the patterns out of
# the caller's own command line. (pgrep/pkill exclude only their own
# transient process by default, not their parent shell -- this script's
# own invocation argv, `bash scripts/stop-dev.sh`, never contains any of
# the literal patterns below, so it's never at risk either way.)
#
# kill -9 throughout: must not depend on any of these tools' own graceful-
# shutdown code running to completion -- that dependency is the bug this
# script exists to fix, not something to reintroduce.
#
# Safety notes (from two rounds of adversarial review):
# - Never signal PGID 0 or 1. A port-holder started without job control
#   under a PID-1 parent (e.g. this repo's own docker-compose `sh -c
#   "npm install && npm run dev"`) has PGID 1 -- `kill -9 -- -1` means
#   "every process the caller can signal", not "this dev server".
# - Never signal our own process group. In a non-interactive shell (CI
#   step, `bash -c "make dev & ...; make stop"`), a backgrounded child can
#   inherit the invoking shell's PGID, which would otherwise SIGKILL this
#   script (and its caller) mid-run.
# - A process-name check alone (comm looks like python/node/uvicorn/
#   next-server) is NOT enough scoping: on a shared machine or with
#   multiple worktrees, an unrelated Node/Python process -- another
#   project's dev server, another checkout of this same repo -- can match
#   by name alone. Every pid this script is about to signal (whether
#   found via the port or via the name backstop) must also have its cwd
#   resolve under THIS checkout's root before it's touched. uvicorn runs
#   with cwd=packages/core and next dev with cwd=packages/ui (both `make
#   dev` recipe lines `cd` there first), so both resolve under REPO_ROOT.
# - Because scoping is enforced by cwd rather than by embedding this
#   checkout's path into a pkill/pgrep regex, the name patterns below can
#   stay simple substrings -- no path-escaping-into-a-regex footgun.
# - A miss (name doesn't look right, or cwd isn't ours) is reported to
#   stderr and the pid is left alone, rather than silently skipped -- and
#   the final "Stopped." message is only printed after re-checking that
#   ports 8000/3000 are actually clear, so a name/scope miss can't produce
#   a false "Stopped." while the real culprit (issue #7's original bug)
#   survives untouched.
#
# Known accepted residual risk (dev-tooling, not worth the added
# complexity here):
# - A pid can theoretically be reused between the ss-lookup and the kill
#   (TOCTOU), and `ss`'s raw users:() text is scanned with a regex rather
#   than a fully escaped parse. Both require an adversarial process
#   already running as the same or a more privileged user on this
#   machine, and are additionally blocked by the cwd check above in all
#   but a deliberately-crafted-comm scenario under sudo.
# - The cwd check runs on the single pid resolved via ss/pgrep, then
#   `kill -9 -- -$PGID` signals every process sharing that pid's process
#   group -- which is not individually re-checked. In the normal case
#   this group is exactly the dev-server's own supervisor/worker/child
#   tree (confirmed by testing: uvicorn's `--reload` supervisor and
#   Next's CLI wrapper both leave their children in the same pgid absent
#   job control), which is what makes the group kill necessary in the
#   first place -- a plain per-pid kill leaves the supervisor alive to
#   respawn a worker. But if an operator's shell also put some unrelated
#   command in that same pgid (e.g. `make dev & other-thing` typed at a
#   job-control-less prompt), that unrelated command would be signalled
#   too. Chose the group kill anyway because it's the only thing that
#   reliably reaches uvicorn's reload supervisor in practice; a purely
#   recursive child-tree kill was tried and rejected here because the
#   supervisor (`uv run uvicorn ...`, comm "uv") isn't a descendant of
#   the pid ss reports and isn't reliably identifiable by name alone.

#
# Portability (issue #27): everything above was validated on Linux only, and
# leaned on four Linux/GNU-isms that made `make stop` silently do nothing on
# macOS (and any other BSD-userland system): `grep -P` (BSD grep rejects
# -P), `ss` (not installed), `/proc/<pid>/cwd` (no /proc), and `ps -o comm=`
# (macOS prints the full executable path, not a bare name, so every
# candidate was reported as "doesn't match a dev-server binary"). Each is now
# behind a small helper below (pids_listening_on, port_bound, process_cwd,
# and the basename step in looks_like_our_dev_process) that keeps the Linux
# path's behavior unchanged and falls back to lsof / basename elsewhere.
# port_bound additionally consults `netstat -an` because an unprivileged lsof
# (macOS) can't see another user's listener -- without it, a root-owned
# process on 3000/8000 would produce a false "Stopped.". netstat itself can be
# blind in some process lineages (see port_bound); when neither ss nor a
# working netstat is available the script says so on stderr rather than
# silently vouching for ports it cannot fully observe.
# The safety rules above (pgid guards, cwd scoping, no false "Stopped.")
# apply identically on every platform.
#
# Sourcing this file (instead of executing it) only defines the helpers and
# returns before doing anything -- see the guard above the main body -- so
# packages/core/tests/unit/test_stop_dev_script.py can exercise them on an
# ephemeral port without touching real dev servers on 8000/3000.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
OWN_PGID="$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')"

# Prints the pids LISTENing on TCP port $1, one per line. `ss` first (the
# path issue #7 validated -- see header); `lsof` if ss is absent OR found
# nothing (e.g. ss present but blind inside a restricted netns/container).
pids_listening_on() {
  local port="$1" out=""
  if command -v ss >/dev/null 2>&1; then
    # `grep -oE | cut`, not `grep -oP 'pid=\K...'`: -P is GNU-only.
    out=$(ss -ltnp 2>/dev/null | awk -v p=":${port}\$" '$4 ~ p' | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)
  fi
  if [ -z "$out" ] && command -v lsof >/dev/null 2>&1; then
    out=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | sort -u)
  fi
  [ -z "$out" ] || printf '%s\n' "$out"
}

# True if ANYTHING is still LISTENing on TCP port $1 -- including sockets
# whose owning pid we can't see (another user's process). Every available
# detector is consulted and any one of them saying "bound" wins, because an
# unprivileged `lsof` (macOS) is blind to other users' sockets: a root-owned
# process on the port would otherwise look free and we'd print a false
# "Stopped." -- the exact failure this script must never produce. `netstat
# -an` needs no privileges and, where it works, sees every listener -- but it
# is NOT infallible: on macOS it returns no TCP sockets at all from some
# process lineages (observed under a uv-managed Python), which is why it is
# only the third opinion and why netstat_is_blind (below) exists. Where ss is
# absent AND netstat is blind, a listener owned by another user cannot be
# ruled out from here -- an accepted residual risk that the script reports
# instead of hiding.
port_bound() {
  local port="$1"
  if command -v ss >/dev/null 2>&1 \
    && ss -ltn 2>/dev/null | grep -qE ":${port}[[:space:]]"; then
    return 0
  fi
  if command -v lsof >/dev/null 2>&1 \
    && [ -n "$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null)" ]; then
    return 0
  fi
  if command -v netstat >/dev/null 2>&1 \
    && netstat -an 2>/dev/null | grep -qE "[.:]${port}[[:space:]].*LISTEN"; then
    return 0
  fi
  return 1
}

# True if netstat exists but reports no TCP sockets at all -- i.e. it is blind
# in this process context (see port_bound). Absent netstat is "not blind",
# just not a detector.
netstat_is_blind() {
  command -v netstat >/dev/null 2>&1 || return 1
  ! netstat -an 2>/dev/null | grep -qE '^tcp'
}

# Prints pid $1's current working directory, or nothing if it can't be
# determined. /proc where it exists (Linux), lsof elsewhere (macOS/BSD).
process_cwd() {
  if [ -L "/proc/$1/cwd" ]; then
    readlink -f "/proc/$1/cwd" 2>/dev/null
  else
    lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1
  fi
}

# True if pid $1 no longer exists or is a zombie awaiting reap (typically:
# just killed by an earlier iteration's group kill). A zombie still passes
# `kill -0`, and on macOS its `ps comm` becomes "(python3.14)" -- which would
# otherwise be misreported as "doesn't match a dev-server binary".
process_is_gone() {
  local stat
  stat=$(ps -o stat= -p "$1" 2>/dev/null | tr -d ' ')
  case "$stat" in
    ""|Z*) return 0 ;;
    *) return 1 ;;
  esac
}

looks_like_our_dev_process() {
  local comm
  comm=$(ps -o comm= -p "$1" 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  # macOS prints the full executable path here (Linux prints a bare name);
  # compare on the basename so both match. Not `tr -d ' '` any more: that
  # would mangle a path with a space in it (e.g. /Users/Some User/...).
  comm=${comm##*/}
  case "$comm" in
    python*|Python*|uvicorn|node*|next-server*) return 0 ;;
    *) return 1 ;;
  esac
}

belongs_to_repo() {
  # Returns: 0 = yes; 1 = no, resolved to a different path; 2 = couldn't
  # tell (already gone, or zombied by an earlier iteration's group kill).
  #
  # A zombie still passes `kill -0` but has no real cwd -- and confirmed
  # experimentally, `readlink -f /proc/<zombie-pid>/cwd` does NOT fail in
  # that case: it falls back to printing the literal, unresolved path
  # ("/proc/<pid>/cwd") with exit 0, which is non-empty and would
  # otherwise be misread as "cwd resolved to some other, wrong path".
  # Checking process state directly avoids relying on that fallback
  # shape (which is a coreutils implementation detail, not a contract).
  process_is_gone "$1" && return 2
  local cwd
  cwd=$(process_cwd "$1")
  [ -n "$cwd" ] || return 2
  case "$cwd" in
    "$REPO_ROOT"|"$REPO_ROOT"/*) return 0 ;;
    *) return 1 ;;
  esac
}

kill_pid() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null || return  # already gone (e.g. reaped by an
                                         # earlier iteration's group kill)
  process_is_gone "$pid" && return       # ...or killed but not yet reaped
  looks_like_our_dev_process "$pid" || {
    # It may have died between the check above and the name lookup (an empty
    # name "doesn't match"); only report a mismatch for a process that's
    # actually still there.
    process_is_gone "$pid" && return
    echo "stop-dev: skipping pid $pid (process name doesn't match a dev-server binary)" >&2
    return
  }
  belongs_to_repo "$pid"
  case $? in
    0) ;;
    2) return ;;  # already gone/zombied -- nothing to report
    *)
      echo "stop-dev: skipping pid $pid (not running out of $REPO_ROOT)" >&2
      return
      ;;
  esac

  local pgid
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
  if [ -n "$pgid" ] && [ "$pgid" -gt 1 ] 2>/dev/null && [ "$pgid" != "$OWN_PGID" ]; then
    kill -9 -- "-${pgid}" 2>/dev/null || true
  fi
  kill -9 "$pid" 2>/dev/null || true
}

kill_port() {
  local port="$1"
  local pids
  pids=$(pids_listening_on "$port")
  for pid in $pids; do
    kill_pid "$pid"
  done
}

kill_by_name() {
  local pattern="$1"
  local pids
  pids=$(pgrep -u "$(id -u)" -f "$pattern" 2>/dev/null)
  for pid in $pids; do
    kill_pid "$pid"
  done
}

# Sourced, not executed (see the header): the helpers above are defined;
# stop here without killing anything.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  return 0
fi

# Without ss or lsof we can't tell whether anything is listening, and the
# final check below would report a false "Stopped." -- the exact failure
# mode this script's safety notes exist to prevent. Fail loudly instead.
if ! command -v ss >/dev/null 2>&1 && ! command -v lsof >/dev/null 2>&1; then
  echo "stop-dev: needs either 'ss' or 'lsof' to find dev-server ports, found neither" >&2
  exit 1
fi

kill_port 8000
kill_port 3000

kill_by_name "uvicorn openexecutive"
kill_by_name "next-server"
kill_by_name "packages/ui/node_modules/.bin/next dev"

sleep 0.2
if port_bound 8000 || port_bound 3000; then
  echo "stop-dev: WARNING -- port 8000 or 3000 is still bound after cleanup:" >&2
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null | grep -E ':8000|:3000' >&2 || true
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:8000 -iTCP:3000 -sTCP:LISTEN >&2 || true
  fi
  # Listeners we can't attribute to a pid (e.g. another user's) only show up here.
  if command -v netstat >/dev/null 2>&1; then
    netstat -an 2>/dev/null | grep -E '[.:](8000|3000)[[:space:]].*LISTEN' >&2 || true
  fi
  exit 1
fi
if ! command -v ss >/dev/null 2>&1 && netstat_is_blind; then
  echo "stop-dev: note -- netstat reports no TCP sockets in this shell and ss is unavailable," >&2
  echo "          so listeners owned by OTHER users can't be ruled out; run from a normal terminal to be sure" >&2
fi
echo "Stopped."
