import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


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

    def clear(self, session_key: str, ip: str) -> None:
        self._ip.pop((session_key, ip), None)

    def reset(self) -> None:
        self._last_prune = -1e9
        self._ip.clear()
        self._session.clear()
        self._locked_ip.clear()
        self._locked_session.clear()
