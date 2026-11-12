import json
import uuid

from starlette.types import ASGIApp, Receive, Scope, Send

from core.config import settings
from core.logging import request_id_var

EXEMPT_PATHS = ("/health", "/ready", "/metrics")
RETRY_AFTER_SECONDS = 2


class LimitStats:
    def __init__(self) -> None:
        self.active = 0
        self.rejected = 0


stats = LimitStats()


class ConcurrencyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path in EXEMPT_PATHS or path.endswith("/events"):
            await self.app(scope, receive, send)
            return
        if stats.active >= settings.MAX_CONCURRENT_REQUESTS:
            stats.rejected += 1
            request_id = request_id_var.get()
            body = json.dumps(
                {
                    "detail": "Service is overloaded, retry later",
                    "code": "overloaded",
                    "request_id": request_id if request_id != "-" else uuid.uuid4().hex,
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"retry-after", str(RETRY_AFTER_SECONDS).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        stats.active += 1
        try:
            await self.app(scope, receive, send)
        finally:
            stats.active -= 1
