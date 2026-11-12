import hashlib
import time
from collections.abc import Callable
from typing import Annotated

from fastapi import Header, Request

from core.config import settings
from core.exceptions import RateLimitError

WINDOW_SECONDS = 60
PURGE_THRESHOLD = 20000


class RateLimiter:
    def __init__(
        self, clock: Callable[[], float] = time.monotonic, window_seconds: int = WINDOW_SECONDS
    ) -> None:
        self._clock = clock
        self._window = window_seconds
        self._windows: dict[str, tuple[float, int]] = {}
        self.rejected = 0

    def hit(self, key: str, limit: int) -> int | None:
        now = self._clock()
        started, count = self._windows.get(key, (now, 0))
        if now - started >= self._window:
            started, count = now, 0
        count += 1
        self._windows[key] = (started, count)
        if len(self._windows) > PURGE_THRESHOLD:
            self._purge(now)
        if count > limit:
            self.rejected += 1
            return max(1, int(started + self._window - now) + 1)
        return None

    def _purge(self, now: float) -> None:
        expired = [k for k, (started, _) in self._windows.items() if now - started >= self._window]
        for key in expired:
            del self._windows[key]

    def reset(self) -> None:
        self._windows.clear()
        self.rejected = 0


limiter = RateLimiter()
hourly_limiter = RateLimiter(window_seconds=3600)

EXEMPT_PATHS = ("/health", "/ready", "/metrics")


def _check(key: str, limit: int) -> None:
    if not settings.RATE_LIMIT_ENABLED or limit <= 0:
        return
    retry_after = limiter.hit(key, limit)
    if retry_after is not None:
        raise RateLimitError("Too many requests", headers={"Retry-After": str(retry_after)})


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def limit_ip(request: Request) -> None:
    if request.url.path in EXEMPT_PATHS:
        return
    _check(f"ip:{client_ip(request)}", settings.RATE_LIMIT_IP_PER_MINUTE)


async def limit_join(request: Request) -> None:
    _check(f"join:{client_ip(request)}", settings.RATE_LIMIT_JOIN_PER_IP_PER_MINUTE)


async def limit_auth(request: Request) -> None:
    _check(f"auth:{client_ip(request)}", settings.RATE_LIMIT_AUTH_PER_IP_PER_MINUTE)


async def limit_token(x_entry_token: Annotated[str | None, Header()] = None) -> None:
    if x_entry_token:
        digest = hashlib.sha256(x_entry_token.encode()).hexdigest()
        _check(f"token:{digest}", settings.RATE_LIMIT_TOKEN_PER_MINUTE)
