import json
import logging
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    root = logging.getLogger()
    if any(getattr(h, "_queue_app", False) for h in root.handlers):
        return

    handler = logging.StreamHandler()
    handler._queue_app = True  # type: ignore[attr-defined]
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        JsonFormatter()
        if fmt == "json"
        else logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)
