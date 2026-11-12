import json
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e


def sse_events(response, limit_seconds: float = 8.0):
    deadline = time.monotonic() + limit_seconds
    event = "message"
    data = ""
    for line in response.iter_lines():
        if time.monotonic() > deadline:
            return
        if line.startswith(":"):
            yield (":", line[1:].strip())
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
        elif line == "":
            if data:
                yield (event, json.loads(data))
            event, data = "message", ""


def connections(live_server) -> int:
    text = httpx.get(f"{live_server}/metrics").text
    line = next(ln for ln in text.splitlines() if ln.startswith("queue_sse_connections "))
    return int(float(line.split()[1]))


def wait_for(predicate, seconds=6.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def test_student_stream_over_http_updates_heartbeats_and_releases_on_disconnect(
    live_server, api, open_session
):
    session = open_session()
    token = "h" * 20
    api.post(f"/sessions/{session['code']}/join", json={"name": "Stream", "token": token})
    url = f"{live_server}/api/v1/sessions/{session['code']}/events"
    before = connections(live_server)

    with httpx.stream(
        "GET", url, headers={"X-Entry-Token": token}, timeout=httpx.Timeout(10.0)
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        events = sse_events(response)
        name, first = next(events)
        assert (name, first["status"], first["position"]) == ("entry", "waiting", 1)
        assert connections(live_server) == before + 1

        api.post(f"/sessions/{session['code']}/next", headers=session["headers"])
        seen = []
        for name, data in events:
            seen.append(name)
            if name == "entry":
                assert data["status"] == "called"
                break
        assert ":" in seen or seen == ["entry"]

        heartbeat = next(ev for ev in events if ev[0] == ":")
        assert heartbeat == (":", "ping")

    assert wait_for(lambda: connections(live_server) == before)


def test_teacher_stream_over_http_requires_auth_and_follows_the_queue(
    live_server, api, open_session
):
    session = open_session()
    url = f"{live_server}/api/v1/sessions/{session['code']}/queue/events"
    assert httpx.get(url).status_code == 401

    with httpx.stream("GET", url, headers=session["headers"], timeout=httpx.Timeout(10.0)) as r:
        events = sse_events(r)
        _, first = next(events)
        assert first["entries"] == []
        api.post(f"/sessions/{session['code']}/join", json={"name": "Новый", "token": "n" * 20})
        _, second = next(ev for ev in events if ev[0] == "entry")
        assert [e["name"] for e in second["entries"]] == ["Новый"]


def test_stream_for_an_unknown_token_is_404_and_no_connection_is_left(live_server, open_session):
    session = open_session()
    before = connections(live_server)

    response = httpx.get(
        f"{live_server}/api/v1/sessions/{session['code']}/events",
        headers={"X-Entry-Token": "x" * 20},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "entry_not_found"
    assert connections(live_server) == before
