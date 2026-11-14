import hashlib
import hmac
import time

from core.attempts import AttemptGuard
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


guard = AttemptGuard(
    ip_failures=settings.PRESENCE_IP_FAILURES,
    session_failures=settings.PRESENCE_SESSION_FAILURES,
    window_seconds=float(settings.PRESENCE_FAILURE_WINDOW_SECONDS),
    lock_seconds=float(settings.PRESENCE_LOCK_SECONDS),
)
