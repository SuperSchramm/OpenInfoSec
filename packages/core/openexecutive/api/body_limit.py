"""Reject oversized request bodies BEFORE they are buffered (issue #39).

A pydantic ``max_length`` on a field is checked after Starlette has read the whole
body and ``json.loads`` has materialised it (measured: a 120 MB body took the
process from 123 MB to 964 MB before its 422). This ASGI middleware refuses a
declared ``Content-Length`` over the limit outright, and counts streamed bytes
for a body with no (or a lying) ``Content-Length``, so neither is ever fully read.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

logger = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_LIMITED_METHODS = frozenset({"POST", "PUT", "PATCH"})
_BODY = b'{"detail":"Request body too large"}'


class _BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]], limits: dict[str, int]) -> None:
        """``limits`` maps a path prefix to the most request-body bytes it may carry."""
        self.app = app
        self.limits = limits

    def _limit_for(self, scope: Scope) -> int | None:
        if scope["type"] != "http" or scope.get("method") not in _LIMITED_METHODS:
            return None
        path = scope.get("path", "")
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path):  # served under a path-prefixing proxy
            path = path[len(root_path):]
        return next((limit for prefix, limit in self.limits.items() if path.startswith(prefix)), None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = self._limit_for(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject(send)
            return

        received = 0
        rejected = False
        stop_reading = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, rejected, stop_reading
            if stop_reading:
                return {"type": "http.disconnect"}  # a handler that keeps draining sees the end, not more body
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    stop_reading = True
                    logger.warning("request body over the %d-byte limit on %s; aborted", limit, scope.get("path"))
                    # Answer here, not by letting the exception propagate: Starlette/FastAPI
                    # wrap an error raised while reading the body into their own 400, and
                    # the app's later response is then swallowed by tracking_send. A handler
                    # that already began responding can't take a 413 too.
                    if not started:
                        rejected = True
                        await self._reject(send)
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if rejected:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started and not rejected:
                await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(_BODY)).encode())],
        })
        await send({"type": "http.response.body", "body": _BODY})
