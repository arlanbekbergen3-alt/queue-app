import hashlib
import hmac
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from core.config import settings

CODE_DIGITS = 6
MIN_INTERVAL_SECONDS = 30
MAX_INTERVAL_SECONDS = 60


def wall_clock() -> float:
    return time.time()


def window_index(moment: float, interval: int) -> int:
    return int(moment // interval)


def code_for(secret: str, session_id: str, window: int) -> str:
    digest = hmac.new(
        secret.encode(), f"presence:{session_id}:{window}".encode(), hashlib.sha256
    ).digest()
    number = int.from_bytes(digest[:8], "big") % 10**CODE_DIGITS
    return f"{number:0{CODE_DIGITS}d}"


def current_code(secret: str, session_id: str, interval: int, moment: float) -> str:
    return code_for(secret, session_id, window_index(moment, interval))


def seconds_left(interval: int, moment: float) -> int:
    return max(1, int(interval - (moment % interval)))


def is_valid(secret: str, session_id: str, interval: int, moment: float, code: str) -> bool:
    if len(code) != CODE_DIGITS or any(ch not in "0123456789" for ch in code):
        return False
    window = window_index(moment, interval)
    ok = False
    for candidate in (window, window - 1):
        ok |= hmac.compare_digest(code_for(secret, session_id, candidate), code)
    return ok


@dataclass
class AttemptGuard:
    ip_failures: int = 5
    session_failures: int = 30
    window_seconds: float = 60.0
    lock_seconds: float = 120.0
    clock: Callable[[], float] = time.monotonic
    _ip: dict[tuple[str, str], deque[float]] = field(default_factory=dict)
    _session: dict[str, deque[float]] = field(default_factory=dict)
    _locked_ip: dict[tuple[str, str], float] = field(default_factory=dict)
    _locked_session: dict[str, float] = field(default_factory=dict)
    _last_prune: float = -1e9

    def retry_after(self, session_key: str, ip: str) -> int:
        now = self.clock()
        until = max(
            self._locked_ip.get((session_key, ip), 0.0), self._locked_session.get(session_key, 0.0)
        )
        return max(0, int(until - now) + 1) if until > now else 0

    def failed(self, session_key: str, ip: str) -> None:
        now = self.clock()
        self._prune(now)
        ip_key = (session_key, ip)
        ip_hits = self._ip.setdefault(ip_key, deque())
        ip_hits.append(now)
        self._trim(ip_hits, now)
        if len(ip_hits) >= self.ip_failures:
            self._locked_ip[ip_key] = now + self.lock_seconds
            ip_hits.clear()
        session_hits = self._session.setdefault(session_key, deque())
        session_hits.append(now)
        self._trim(session_hits, now)
        if len(session_hits) >= self.session_failures:
            self._locked_session[session_key] = now + self.lock_seconds
            session_hits.clear()

    def _trim(self, hits: deque[float], now: float) -> None:
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()

    def _prune(self, now: float) -> None:
        if len(self._ip) + len(self._session) < 20000 or now - self._last_prune < 1.0:
            return
        self._last_prune = now
        for ip_key in [
            k for k, v in self._ip.items() if not v or now - v[-1] > self.window_seconds
        ]:
            del self._ip[ip_key]
        for key in [
            k for k, v in self._session.items() if not v or now - v[-1] > self.window_seconds
        ]:
            del self._session[key]
        for ip_key in [k for k, v in self._locked_ip.items() if v <= now]:
            del self._locked_ip[ip_key]
        for key in [k for k, v in self._locked_session.items() if v <= now]:
            del self._locked_session[key]

    def reset(self) -> None:
        self._last_prune = -1e9
        self._ip.clear()
        self._session.clear()
        self._locked_ip.clear()
        self._locked_session.clear()


guard = AttemptGuard(
    ip_failures=settings.PRESENCE_IP_FAILURES,
    session_failures=settings.PRESENCE_SESSION_FAILURES,
    window_seconds=float(settings.PRESENCE_FAILURE_WINDOW_SECONDS),
    lock_seconds=float(settings.PRESENCE_LOCK_SECONDS),
)
