from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from openexecutive.api.models import SessionSummary
from openexecutive.api.routes.chat import (
    _resolve_caller_person_id,
    _session_access,
    forget_session,
)
from openexecutive.memory.session_store import (
    delete_session,
    get_session_metadata,
    list_sessions,
    load_messages,
)

router = APIRouter()


@router.get("/sessions", response_model=list[SessionSummary])
def get_sessions(request: Request) -> list[SessionSummary]:
    caller_person_id = _resolve_caller_person_id(request)
    if caller_person_id is None:
        # Either a signed-in user whose email isn't in the roster, or no
        # principal is configured yet (fresh install). Either way they
        # have no chats to see — return empty rather than leaking the
        # legacy NULL-owner rows.
        return []
    return [SessionSummary(**s) for s in list_sessions(caller_person_id)]


def _require_session_access(request: Request, session_id: str) -> int | None:
    """404 unless the caller may use this session; returns the caller's id.

    Session ids are guessable (`slack:dm:<user id>`, `telegram:<chat id>`), so
    every per-session route checks ownership rather than trusting the id. An
    unknown session and someone else's answer the same, so the routes can't be
    used to probe which chats exist."""
    caller = _resolve_caller_person_id(request)
    if _session_access(request, session_id, caller) != "allowed":
        raise HTTPException(status_code=404, detail="Session not found")
    return caller


@router.get("/sessions/{session_id}", response_model=SessionSummary)
def get_session(session_id: str, request: Request) -> SessionSummary:
    _require_session_access(request, session_id)
    meta = get_session_metadata(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionSummary(**meta)


@router.get("/sessions/{session_id}/messages")
def get_session_messages(session_id: str, request: Request) -> list[dict]:
    _require_session_access(request, session_id)
    meta = get_session_metadata(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return load_messages(session_id)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session_route(session_id: str, request: Request) -> Response:
    _require_session_access(request, session_id)
    deleted = delete_session(session_id)
    # A chat whose row never persisted still counts: dropping its live state
    # is the delete the caller asked for.
    if not forget_session(session_id) and not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
