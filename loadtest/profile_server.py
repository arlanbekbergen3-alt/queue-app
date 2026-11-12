import collections
import cProfile
import pstats

import uvicorn
from sqlalchemy import event

from db.database import engine

connections_opened = 0


@event.listens_for(engine.sync_engine, "connect")
def count_connect(dbapi_connection, connection_record):
    global connections_opened
    connections_opened += 1


GROUPS = [
    ("sqlalchemy", "site-packages/sqlalchemy"),
    ("asyncpg", "site-packages/asyncpg"),
    ("fastapi", "site-packages/fastapi"),
    ("starlette", "site-packages/starlette"),
    ("pydantic", "site-packages/pydantic"),
    ("anyio", "site-packages/anyio"),
    ("uvicorn", "site-packages/uvicorn"),
    ("passlib/bcrypt", "bcrypt"),
    ("scram (hmac/hashlib)", "hmac"),
    ("app code", "/app/"),
    ("logging", "logging/"),
    ("asyncio", "asyncio/"),
]


def group_of(filename: str, funcname: str) -> str:
    for name, marker in GROUPS:
        if marker in filename:
            return name
    if "epoll" in funcname or "select" in funcname:
        return "idle (epoll wait)"
    if "socket" in funcname or "_socket" in filename:
        return "socket send/recv"
    return "other (builtins, etc.)"


profiler = cProfile.Profile()
profiler.enable()
try:
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="warning")
finally:
    profiler.disable()
    stats = pstats.Stats(profiler)
    totals = collections.Counter()
    for (filename, _line, funcname), (_cc, _nc, tottime, _ct, _callers) in stats.stats.items():
        totals[group_of(filename, funcname)] += tottime
    busy = sum(v for k, v in totals.items() if not k.startswith("idle"))
    print(f"CONNECTIONS_OPENED {connections_opened}")
    print(f"BUSY_SECONDS {busy:.2f}")
    for name, seconds in totals.most_common():
        print(f"GROUP {name:28s} {seconds:7.2f}s {seconds / busy * 100:5.1f}% of busy")
    stats.sort_stats("tottime").print_stats(14)
