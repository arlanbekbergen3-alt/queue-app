import base64
import hashlib
import re
from pathlib import Path

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.config import settings

_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)
SKIP_CSP_PATHS = ("/docs", "/redoc", "/openapi.json")


def script_hashes(directory: Path) -> list[str]:
    hashes: set[str] = set()
    for page in sorted(directory.glob("*.html")):
        text = page.read_bytes().decode("utf-8").replace("\r\n", "\n")
        for body in _SCRIPT.findall(text):
            digest = hashlib.sha256(body.encode("utf-8")).digest()
            hashes.add("'sha256-" + base64.b64encode(digest).decode() + "'")
    return sorted(hashes)


def build_csp(hashes: list[str]) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' " + " ".join(hashes),
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


BASE_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"no-referrer",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=()",
    b"cross-origin-opener-policy": b"same-origin",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, static_dir: Path) -> None:
        self.app = app
        self.csp = build_csp(script_hashes(static_dir)).encode()
        self.hsts = (
            f"max-age={settings.HSTS_MAX_AGE_SECONDS}".encode()
            if settings.HSTS_MAX_AGE_SECONDS > 0
            else None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                for name, value in BASE_HEADERS.items():
                    if name not in present:
                        headers.append((name, value))
                content_type = dict(headers).get(b"content-type", b"")
                if content_type.startswith(b"text/html") and not path.startswith(SKIP_CSP_PATHS):
                    headers.append((b"content-security-policy", self.csp))
                if self.hsts is not None:
                    headers.append((b"strict-transport-security", self.hsts))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
