import asyncio
import csv
import os
import statistics
import time

import uvicorn

from db.database import engine
from main import app
from services.live import hub

OUT = os.environ.get("PROBE_OUT", "/out/probe.csv")
LAG_STEP = 0.02

pool = engine.sync_engine.pool
original_get = pool._do_get
waits: list[float] = []
lags: list[float] = []


def timed_get():
    started = time.perf_counter()
    try:
        return original_get()
    finally:
        waits.append((time.perf_counter() - started) * 1000)


pool._do_get = timed_get


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


async def lag_loop() -> None:
    while True:
        started = time.perf_counter()
        await asyncio.sleep(LAG_STEP)
        lags.append((time.perf_counter() - started - LAG_STEP) * 1000)


async def sample_loop() -> None:
    header = [
        "ts",
        "cpu_fraction",
        "lag_p50_ms",
        "lag_p95_ms",
        "lag_max_ms",
        "pool_checked_out",
        "pool_wait_n",
        "pool_wait_over_1ms",
        "pool_wait_p95_ms",
        "pool_wait_max_ms",
        "pool_overflow",
        "sse_subscribers",
        "sse_refreshes",
        "tasks",
    ]
    with open(OUT, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        handle.flush()
        last_wall = time.perf_counter()
        last_cpu = time.process_time()
        while True:
            await asyncio.sleep(1.0)
            wall = time.perf_counter()
            cpu = time.process_time()
            took = list(waits)
            waits.clear()
            seen = list(lags)
            lags.clear()
            writer.writerow(
                [
                    int(time.time()),
                    round((cpu - last_cpu) / (wall - last_wall), 3),
                    round(statistics.median(seen), 2) if seen else 0,
                    round(percentile(seen, 0.95), 2),
                    round(max(seen), 2) if seen else 0,
                    pool.checkedout(),
                    len(took),
                    sum(1 for w in took if w > 1.0),
                    round(percentile(took, 0.95), 2),
                    round(max(took), 2) if took else 0,
                    pool.overflow(),
                    hub.count(),
                    hub.refreshes,
                    len(asyncio.all_tasks()),
                ]
            )
            handle.flush()
            last_wall = wall
            last_cpu = cpu


class Probe:
    def __init__(self, inner):
        self.inner = inner
        self.started = False

    async def __call__(self, scope, receive, send):
        if not self.started:
            self.started = True
            loop = asyncio.get_running_loop()
            loop.create_task(lag_loop())
            loop.create_task(sample_loop())
        await self.inner(scope, receive, send)


if __name__ == "__main__":
    uvicorn.run(Probe(app), host="0.0.0.0", port=8000, log_level="warning", lifespan="on")
