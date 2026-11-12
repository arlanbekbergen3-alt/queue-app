import json
import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.logging import request_id_var
from core.metrics import metrics

logger = logging.getLogger("queue.access")


def error_body(detail: str, code: str, request_id: str | None = None, **extra: object) -> dict:
    return {
        "detail": detail,
        "code": code,
        "request_id": request_id or request_id_var.get(),
        **extra,
    }


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, access_log: bool = True) -> None:
        self.app = app
        self.access_log = access_log

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")[:64]
        cleaned = "".join(ch for ch in supplied if ch.isalnum() or ch in "-_.")
        request_id = cleaned or uuid.uuid4().hex
        request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            logger.exception("Unhandled exception on %s %s", scope["method"], scope["path"])
            status = 500
            if not response_started:
                body = json.dumps(error_body("Internal server error", "internal_error")).encode()
                await send_with_id(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send_with_id({"type": "http.response.body", "body": body})
        finally:
            elapsed = time.perf_counter() - started
            metrics.observe_request(status, elapsed)
            if self.access_log:
                logger.info(
                    "%s %s -> %s (%.1f ms)", scope["method"], scope["path"], status, elapsed * 1000
                )
