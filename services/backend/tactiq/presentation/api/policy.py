from __future__ import annotations

from collections import deque
import time
from threading import Lock

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from tactiq.runtime.observability import ensure_request_id


class RequestLimiter:
    def __init__(self, general_limit: int = 240, expensive_limit: int = 12) -> None:
        if min(general_limit, expensive_limit) < 1:
            raise ValueError("Request rate limits must be positive")
        self.general_limit = general_limit
        self.expensive_limit = expensive_limit
        self.windows: dict[tuple[str, str], deque[float]] = {}
        self.lock = Lock()

    def check(self, identity: str, expensive: bool = False) -> None:
        now = time.monotonic()
        categories = [("all", self.general_limit)]
        if expensive:
            categories.append(("expensive", self.expensive_limit))
        with self.lock:
            for category, limit in categories:
                key = identity, category
                if key not in self.windows and len(self.windows) >= 4096:
                    self.windows = {k: v for k, v in self.windows.items() if v and v[-1] > now - 60}
                    if len(self.windows) >= 4096:
                        raise HTTPException(503, "Request admission capacity reached")
                window = self.windows.setdefault(key, deque())
                while window and window[0] <= now - 60:
                    window.popleft()
                if len(window) >= limit:
                    raise HTTPException(429, "Request rate limit reached", headers={"Retry-After": "60"})
            for category, _ in categories:
                self.windows[(identity, category)].append(now)


class _BodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(413, "Request body exceeds the configured limit")


class ResponseLifetimeMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self.app(scope, receive, send)
        finally:
            release = scope.pop("tactiq_match_release", None)
            if release is not None:
                release()


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, upload_limit: int) -> None:
        self.app = app
        self.upload_limit = upload_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        error_headers = {"X-Request-ID": ensure_request_id(headers.get(b"x-request-id", b"").decode("latin1")),
                         "X-Content-Type-Options": "nosniff"}
        multipart = headers.get(b"content-type", b"").startswith(b"multipart/form-data")
        limit = self.upload_limit + 1024 * 1024 if multipart else 256 * 1024
        declared = headers.get(b"content-length")
        try:
            if declared is not None and int(declared) < 0:
                raise ValueError()
            if declared is not None and int(declared) > limit:
                await JSONResponse({"detail": "Request body exceeds the configured limit"}, status_code=413, headers=error_headers)(scope, receive, send)
                return
        except ValueError:
            await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400, headers=error_headers)(scope, receive, send)
            return
        received = 0
        started = False
        replaced = False

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    scope["body_limit_exceeded"] = True
                    raise _BodyTooLarge()
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal started, replaced
            if message["type"] == "http.response.start":
                started = True
                if scope.get("body_limit_exceeded") and message["status"] != 413:
                    replaced = True
                    await JSONResponse({"detail": "Request body exceeds the configured limit"}, status_code=413,
                                       headers=error_headers)(scope, receive, send)
                    return
            if replaced:
                return
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracked_send)
        except _BodyTooLarge:
            if not started:
                await JSONResponse({"detail": "Request body exceeds the configured limit"}, status_code=413)(scope, receive, send)
            else:
                raise
