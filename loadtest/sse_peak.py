import asyncio
import json
import os
import random
import time

import httpx

BASE = os.environ.get("BASE_URL", "http://localhost:8000")
INVITE = os.environ["TEACHER_INVITE_CODE"]
STUDENTS = int(os.environ.get("STUDENTS", "300"))
ARRIVAL = float(os.environ.get("ARRIVAL_SECONDS", "60"))
DURATION = float(os.environ.get("DURATION_SECONDS", "240"))
TEACHER_START = 15.0
TEACHER_INTERVAL = 60.0
SAFETY_POLL = 60.0
OUT = os.environ.get("OUT", "")
SESSION_OPTIONS = json.loads(os.environ.get("SESSION_OPTIONS", "{}"))
PRESENCE = os.environ.get("PRESENCE", "0") == "1"

API = f"{BASE}/api/v1"
timings: dict[str, list[float]] = {
    k: [] for k in ("join", "me", "next", "done", "propagation", "teacher_propagation")
}
errors: dict[str, int] = {}
counters = {"events": 0, "heartbeats": 0, "stream_drops": 0, "stream_opened": 0, "requests": 0}
action = {"sent": 0.0}


def record_error(name: str, status: object) -> None:
    key = f"{name}:{status}"
    errors[key] = errors.get(key, 0) + 1


async def timed(client: httpx.AsyncClient, name: str, method: str, url: str, **kwargs):
    started = time.perf_counter()
    counters["requests"] += 1
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        record_error(name, type(exc).__name__)
        return None
    timings[name].append((time.perf_counter() - started) * 1000)
    if response.status_code != 200:
        record_error(name, response.status_code)
    return response


async def read_stream(client, url, headers, on_event, stop: asyncio.Event):
    try:
        async with client.stream(
            "GET", url, headers=headers, timeout=httpx.Timeout(30, read=None)
        ) as r:
            if r.status_code != 200:
                record_error("stream", r.status_code)
                return
            counters["stream_opened"] += 1
            event, data = "message", ""
            async for line in r.aiter_lines():
                if stop.is_set():
                    return
                if line.startswith(":"):
                    counters["heartbeats"] += 1
                elif line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
                elif line == "":
                    if event == "end":
                        return
                    if data:
                        counters["events"] += 1
                        on_event(event, json.loads(data))
                    event, data = "message", ""
    except httpx.HTTPError as exc:
        record_error("stream", type(exc).__name__)


async def student(
    i: int, client: httpx.AsyncClient, code: str, run_id: str, stop: asyncio.Event, auth: dict
):
    token = f"lt-{run_id}-{i}".ljust(24, "x")
    await asyncio.sleep(random.random() * ARRIVAL)
    body = {"name": f"Student {i}", "token": token}
    if PRESENCE:
        shown = await client.get(f"{API}/sessions/{code}/presence", headers=auth)
        body["presence_code"] = shown.json().get("code")
    joined = await timed(client, "join", "POST", f"{API}/sessions/{code}/join", json=body)
    if joined is None or joined.status_code != 200:
        return
    last = {"payload": None}

    def on_event(_name: str, data: dict) -> None:
        now = time.perf_counter()
        if data != last["payload"]:
            if last["payload"] is not None and 0 <= now - action["sent"] <= 5:
                timings["propagation"].append((now - action["sent"]) * 1000)
            last["payload"] = data

    async def safety_polls():
        while not stop.is_set():
            await asyncio.sleep(SAFETY_POLL + random.random() * 5)
            await timed(
                client, "me", "GET", f"{API}/sessions/{code}/me", headers={"X-Entry-Token": token}
            )

    poller = asyncio.create_task(safety_polls())
    try:
        while not stop.is_set():
            await read_stream(
                client, f"{API}/sessions/{code}/events", {"X-Entry-Token": token}, on_event, stop
            )
            if stop.is_set():
                break
            counters["stream_drops"] += 1
            await asyncio.sleep(5 + random.random())
    finally:
        poller.cancel()


async def teacher(client, code: str, auth: dict, stop: asyncio.Event):
    base = f"{API}/sessions/{code}"

    def on_event(_name: str, data: dict) -> None:
        now = time.perf_counter()
        if 0 <= now - action["sent"] <= 5:
            timings["teacher_propagation"].append((now - action["sent"]) * 1000)

    watcher = asyncio.create_task(read_stream(client, f"{base}/queue/events", auth, on_event, stop))
    await asyncio.sleep(TEACHER_START)
    current = None
    try:
        while not stop.is_set():
            cycle = time.perf_counter()
            if current:
                action["sent"] = time.perf_counter()
                await timed(client, "done", "POST", f"{base}/entries/{current}/done", headers=auth)
                current = None
            action["sent"] = time.perf_counter()
            response = await timed(client, "next", "POST", f"{base}/next", headers=auth)
            if response is not None and response.status_code == 200:
                current = response.json()["id"]
            await asyncio.sleep(max(0, TEACHER_INTERVAL - (time.perf_counter() - cycle)))
    finally:
        watcher.cancel()


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)


def summarize(elapsed: float) -> dict:
    out = {
        name: {
            "count": len(values),
            "p50": percentile(values, 0.50),
            "p95": percentile(values, 0.95),
            "p99": percentile(values, 0.99),
            "max": round(max(values), 1) if values else None,
        }
        for name, values in timings.items()
    }
    return {
        "elapsed_seconds": round(elapsed, 1),
        "students": STUDENTS,
        "timings_ms": out,
        "errors": errors,
        "counters": counters,
        "http_requests_per_second": round(counters["requests"] / elapsed, 2),
    }


async def main() -> None:
    limits = httpx.Limits(max_connections=STUDENTS + 50, max_keepalive_connections=100)
    async with httpx.AsyncClient(limits=limits, timeout=30) as client:
        run_id = format(int(time.time()), "x")
        username, password = f"lt_{run_id}", "loadtest-password"
        reg = await client.post(
            f"{API}/auth/register",
            json={"username": username, "password": password, "invite_code": INVITE},
        )
        assert reg.status_code == 201, reg.text
        login = await client.post(
            f"{API}/auth/login", json={"username": username, "password": password}
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        session = await client.post(
            f"{API}/sessions",
            json={
                "title": "Load test SSE",
                **(
                    {"presence_check": "rotating_code", "presence_interval": 60} if PRESENCE else {}
                ),
                **SESSION_OPTIONS,
            },
            headers=auth,
        )
        code = session.json()["code"]

        stop = asyncio.Event()
        started = time.perf_counter()
        tasks = [
            asyncio.create_task(student(i, client, code, run_id, stop, auth))
            for i in range(STUDENTS)
        ]
        tasks.append(asyncio.create_task(teacher(client, code, auth, stop)))
        await asyncio.sleep(ARRIVAL + DURATION)
        stop.set()
        elapsed = time.perf_counter() - started
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        metrics = await client.get(f"{BASE}/metrics")
    result = summarize(elapsed)
    result["server_metrics"] = {
        line.split()[0]: line.split()[1]
        for line in metrics.text.splitlines()
        if line and not line.startswith("#") and "_bucket" not in line
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if OUT:
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)


asyncio.run(main())
