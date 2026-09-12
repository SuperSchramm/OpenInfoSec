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

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
OWN_PGID="$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')"

looks_like_our_dev_process() {
  local comm
  comm=$(ps -o comm= -p "$1" 2>/dev/null | tr -d ' ')
  case "$comm" in
    python*|uvicorn|node*|next-server*) return 0 ;;
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
  local stat
  stat=$(ps -o stat= -p "$1" 2>/dev/null | tr -d ' ')
  case "$stat" in
    ""|Z*) return 2 ;;
  esac
  local cwd
  cwd=$(readlink -f "/proc/$1/cwd" 2>/dev/null)
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
  looks_like_our_dev_process "$pid" || {
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
  pids=$(ss -ltnp 2>/dev/null | awk -v p=":${port}\$" '$4 ~ p' | grep -oP 'pid=\K[0-9]+' | sort -u)
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

kill_port 8000
kill_port 3000

kill_by_name "uvicorn openexecutive"
kill_by_name "next-server"
kill_by_name "packages/ui/node_modules/.bin/next dev"

sleep 0.2
if ss -ltn 2>/dev/null | grep -qE ':(8000|3000)[[:space:]]'; then
  echo "stop-dev: WARNING -- port 8000 or 3000 is still bound after cleanup:" >&2
  ss -ltnp 2>/dev/null | grep -E ':8000|:3000' >&2 || true
  exit 1
fi
echo "Stopped."
