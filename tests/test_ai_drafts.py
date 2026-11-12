import asyncio
import os
import uuid

import httpx
import pytest
import pytest_asyncio

from core.config import settings
from services import ai
from services.ai import (
    AIBadResponseError,
    AITimeoutError,
    AIUnavailableError,
    OllamaProvider,
    parse_json_object,
    validate_draft,
)
from services.ai_drafts import runtime

API = "/api/v1"
SOURCE = "Лабораторная работа: реализовать очередь на сдачу с блокировками и тестами гонок."


def good_draft(n: int = 12) -> dict:
    return {
        "questions": [
            {"text": f"Объясните пункт {i} работы подробно", "difficulty": 1 + i % 3, "topic": "т"}
            for i in range(n)
        ],
        "criteria": [{"name": "Код", "max_points": 5}, {"name": "Защита", "max_points": 5}],
    }


class FakeProvider:
    def __init__(self, result=None, error: Exception | None = None, delay: float = 0.0):
        self.result = result
        self.error = error
        self.delay = delay
        self.calls: list[dict] = []

    async def generate_json(self, *, system, user, schema, max_tokens):
        self.calls.append({"system": system, "user": user, "schema": schema, "max": max_tokens})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def ai_on(monkeypatch):
    monkeypatch.setattr(settings, "AI_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)

    def install(provider):
        monkeypatch.setattr(ai, "provider_factory", lambda: provider)
        return provider

    return install


@pytest_asyncio.fixture
async def assignment(client, make_teacher):
    headers = await make_teacher(f"ai_t_{uuid.uuid4().hex[:8]}")
    r = await client.post(f"{API}/assignments", headers=headers, json={"title": "ИИ работа"})
    return headers, r.json()["id"]


async def run_draft(client, headers, aid, source=SOURCE):
    r = await client.post(
        f"{API}/assignments/{aid}/ai/draft", headers=headers, json={"source_text": source}
    )
    assert r.status_code == 202, r.text
    await runtime.wait_idle()
    job = await client.get(f"{API}/ai/jobs/{r.json()['id']}", headers=headers)
    return r.json()["id"], job.json()


@pytest.mark.asyncio
async def test_draft_succeeds_and_nothing_enters_the_bank_until_approval(client, assignment, ai_on):
    headers, aid = assignment
    provider = ai_on(FakeProvider(result=good_draft()))

    r = await client.post(
        f"{API}/assignments/{aid}/ai/draft", headers=headers, json={"source_text": SOURCE}
    )
    assert r.status_code == 202 and r.json()["status"] in ("queued", "running", "done")
    await runtime.wait_idle()
    job = (await client.get(f"{API}/ai/jobs/{r.json()['id']}", headers=headers)).json()
    assert job["status"] == "done" and len(job["result"]["questions"]) == 12
    assert job["approved"] is False

    assert (await client.get(f"{API}/assignments/{aid}/questions", headers=headers)).json() == []
    assert (await client.get(f"{API}/assignments/{aid}/criteria", headers=headers)).json() == []

    assert SOURCE in provider.calls[0]["user"]

    edited = {
        "questions": job["result"]["questions"][:3],
        "criteria": job["result"]["criteria"],
    }
    r = await client.post(f"{API}/ai/jobs/{job['id']}/approve", headers=headers, json=edited)
    assert r.status_code == 200
    assert r.json() == {
        "added_questions": 3,
        "skipped_questions": 0,
        "added_criteria": 2,
        "skipped_criteria": 0,
    }
    assert (
        len((await client.get(f"{API}/assignments/{aid}/questions", headers=headers)).json()) == 3
    )

    r = await client.post(f"{API}/ai/jobs/{job['id']}/approve", headers=headers, json=edited)
    assert (r.status_code, r.json()["code"]) == (409, "already_approved")


@pytest.mark.asyncio
async def test_only_the_source_text_is_sent_to_the_model(client, assignment, ai_on):
    headers, aid = assignment
    provider = ai_on(FakeProvider(result=good_draft()))
    await run_draft(client, headers, aid)
    sent = provider.calls[0]["system"] + provider.calls[0]["user"]
    assert "ai_t_" not in sent
    assert aid not in sent
    assert SOURCE in provider.calls[0]["user"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AIUnavailableError("down"), "ai_unavailable"),
        (AITimeoutError("slow"), "ai_timeout"),
        (AIBadResponseError("junk"), "ai_bad_response"),
        (RuntimeError("boom"), "ai_internal"),
    ],
)
async def test_provider_failures_become_a_job_error_not_a_500(
    client, assignment, ai_on, error, code
):
    headers, aid = assignment
    ai_on(FakeProvider(error=error))
    _, job = await run_draft(client, headers, aid)
    assert job["status"] == "error" and job["error_code"] == code
    assert job["result"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "junk",
    [
        {},
        {"questions": "text"},
        {"questions": [{"text": "x", "difficulty": 2}] * 5},
        {"questions": [{"text": "Нормальный вопрос про блокировки", "difficulty": 9}] * 5},
        {"questions": [{"text": "Вопрос с \x00 управляющим символом", "difficulty": 1}] * 5},
        {"questions": [{"text": "Q" * 1001, "difficulty": 1}] * 5},
    ],
)
async def test_garbage_answers_are_rejected_with_a_clear_error(client, assignment, ai_on, junk):
    headers, aid = assignment
    ai_on(FakeProvider(result=junk))
    _, job = await run_draft(client, headers, aid)
    assert job["status"] == "error" and job["error_code"] == "ai_bad_response"
    assert (await client.get(f"{API}/assignments/{aid}/questions", headers=headers)).json() == []


def test_validate_draft_drops_duplicates_and_keeps_order():
    raw = good_draft(5)
    raw["questions"].append(dict(raw["questions"][0], text=raw["questions"][0]["text"].upper()))
    raw["criteria"].append({"name": "код", "max_points": 3})
    draft = validate_draft(raw)
    assert len(draft.questions) == 5 and len(draft.criteria) == 2


class SequenceProvider(FakeProvider):
    def __init__(self, *answers):
        super().__init__()
        self.answers = list(answers)

    async def generate_json(self, *, system, user, schema, max_tokens):
        self.calls.append({"system": system, "user": user, "schema": schema, "max": max_tokens})
        answer = self.answers[min(len(self.calls), len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_default_minimum_is_eight():
    assert settings.AI_MIN_QUESTIONS == 8


@pytest.mark.asyncio
async def test_enough_questions_need_no_retry(client, assignment, ai_on):
    headers, aid = assignment
    provider = ai_on(SequenceProvider(good_draft(8)))
    _, job = await run_draft(client, headers, aid)
    assert len(provider.calls) == 1
    assert job["status"] == "done" and job["result"]["below_recommended"] is False
    assert job["result"]["min_questions"] == 8


@pytest.mark.asyncio
async def test_too_few_questions_trigger_one_retry_with_a_reminder(client, assignment, ai_on):
    headers, aid = assignment
    provider = ai_on(SequenceProvider(good_draft(3), good_draft(10)))
    _, job = await run_draft(client, headers, aid)
    assert len(provider.calls) == 2
    assert "reminder" not in provider.calls[0]["user"]
    assert "reminder" in provider.calls[1]["user"] and "8" in provider.calls[1]["user"]
    assert job["status"] == "done" and len(job["result"]["questions"]) == 10
    assert job["result"]["below_recommended"] is False


@pytest.mark.asyncio
async def test_still_too_few_after_retry_shows_what_there_is_with_a_flag(client, assignment, ai_on):
    headers, aid = assignment
    provider = ai_on(SequenceProvider(good_draft(3), good_draft(5), good_draft(12)))
    _, job = await run_draft(client, headers, aid)
    assert len(provider.calls) == 2
    assert job["status"] == "done" and len(job["result"]["questions"]) == 5
    assert job["result"]["below_recommended"] is True and job["result"]["min_questions"] == 8


@pytest.mark.asyncio
async def test_a_worse_retry_does_not_replace_the_first_draft(client, assignment, ai_on):
    headers, aid = assignment
    ai_on(SequenceProvider(good_draft(6), good_draft(2)))
    _, job = await run_draft(client, headers, aid)
    assert len(job["result"]["questions"]) == 6 and job["result"]["below_recommended"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [AIUnavailableError("down"), AITimeoutError("slow")])
async def test_a_failed_retry_keeps_the_first_draft(client, assignment, ai_on, failure):
    headers, aid = assignment
    provider = ai_on(SequenceProvider(good_draft(4), failure))
    _, job = await run_draft(client, headers, aid)
    assert len(provider.calls) == 2
    assert job["status"] == "done" and len(job["result"]["questions"]) == 4
    assert job["result"]["below_recommended"] is True


@pytest.mark.asyncio
async def test_no_usable_questions_even_after_retry_is_an_error(client, assignment, ai_on):
    headers, aid = assignment
    provider = ai_on(SequenceProvider({"questions": []}, {"questions": []}))
    _, job = await run_draft(client, headers, aid)
    assert len(provider.calls) == 2
    assert job["status"] == "error" and job["error_code"] == "ai_bad_response"


@pytest.mark.asyncio
async def test_minimum_comes_from_settings(client, assignment, ai_on, monkeypatch):
    monkeypatch.setattr(settings, "AI_MIN_QUESTIONS", 4)
    headers, aid = assignment
    provider = ai_on(SequenceProvider(good_draft(4)))
    _, job = await run_draft(client, headers, aid)
    assert len(provider.calls) == 1 and job["result"]["below_recommended"] is False
    monkeypatch.setattr(settings, "AI_MIN_QUESTIONS", 5)
    provider = ai_on(SequenceProvider(good_draft(4), good_draft(4)))
    _, job = await run_draft(client, headers, aid)
    assert len(provider.calls) == 2 and job["result"]["below_recommended"] is True


def test_parse_json_object_handles_fences_think_blocks_and_size(monkeypatch):
    assert parse_json_object('<think>x</think>```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(AIBadResponseError):
        parse_json_object("[1, 2]")
    with pytest.raises(AIBadResponseError):
        parse_json_object("not json")
    monkeypatch.setattr(settings, "AI_MAX_RESPONSE_CHARS", 100)
    with pytest.raises(AIBadResponseError):
        parse_json_object('{"a": "' + "x" * 200 + '"}')


@pytest.mark.asyncio
async def test_disabled_feature_gives_a_clear_503_and_the_rest_keeps_working(
    client, assignment, monkeypatch
):
    headers, aid = assignment
    monkeypatch.setattr(settings, "AI_ENABLED", False)
    r = await client.post(
        f"{API}/assignments/{aid}/ai/draft", headers=headers, json={"source_text": SOURCE}
    )
    assert (r.status_code, r.json()["code"]) == (503, "ai_disabled")
    status = (await client.get(f"{API}/ai/status", headers=headers)).json()
    assert status["enabled"] is False and status["model"] is None
    q = await client.post(
        f"{API}/assignments/{aid}/questions", headers=headers, json={"text": "Обычный вопрос"}
    )
    assert q.status_code == 201


@pytest.mark.asyncio
async def test_source_text_limits_are_validated(client, assignment, ai_on):
    headers, aid = assignment
    ai_on(FakeProvider(result=good_draft()))
    r = await client.post(
        f"{API}/assignments/{aid}/ai/draft", headers=headers, json={"source_text": "коротко"}
    )
    assert r.status_code == 422 and r.json()["errors"][0]["type"] == "string_too_short"
    r = await client.post(
        f"{API}/assignments/{aid}/ai/draft",
        headers=headers,
        json={"source_text": "x" * (settings.AI_MAX_SOURCE_CHARS + 1)},
    )
    assert r.status_code == 422 and r.json()["errors"][0]["type"] == "string_too_long"


@pytest.mark.asyncio
async def test_hourly_limit_per_teacher(client, assignment, ai_on, monkeypatch):
    headers, aid = assignment
    ai_on(FakeProvider(result=good_draft()))
    monkeypatch.setattr(settings, "AI_DRAFTS_PER_TEACHER_PER_HOUR", 2)
    for _ in range(2):
        await run_draft(client, headers, aid)
    r = await client.post(
        f"{API}/assignments/{aid}/ai/draft", headers=headers, json={"source_text": SOURCE}
    )
    assert (r.status_code, r.json()["code"]) == (429, "ai_rate_limited")
    assert "Retry-After" in r.headers


@pytest.mark.asyncio
async def test_only_one_generation_runs_at_a_time(client, assignment, ai_on):
    headers, aid = assignment
    running = 0
    peak = 0

    class Slow(FakeProvider):
        async def generate_json(self, **kwargs):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.15)
            running -= 1
            return good_draft()

    ai_on(Slow())
    responses = await asyncio.gather(
        *(
            client.post(
                f"{API}/assignments/{aid}/ai/draft", headers=headers, json={"source_text": SOURCE}
            )
            for _ in range(3)
        )
    )
    assert {r.status_code for r in responses} == {202}
    await runtime.wait_idle()
    assert peak == 1
    states = [
        (await client.get(f"{API}/ai/jobs/{r.json()['id']}", headers=headers)).json()["status"]
        for r in responses
    ]
    assert states == ["done"] * 3


@pytest.mark.asyncio
async def test_too_many_waiting_jobs_are_refused_as_busy(client, assignment, ai_on):
    headers, aid = assignment
    ai_on(FakeProvider(result=good_draft(), delay=0.6))
    codes = []
    for _ in range(5):
        r = await client.post(
            f"{API}/assignments/{aid}/ai/draft", headers=headers, json={"source_text": SOURCE}
        )
        codes.append(r.status_code)
    await runtime.wait_idle()
    assert codes == [202, 202, 202, 429, 429]


@pytest.mark.asyncio
async def test_a_job_of_another_teacher_is_404(client, assignment, ai_on, make_teacher):
    headers, aid = assignment
    ai_on(FakeProvider(result=good_draft()))
    job_id, _ = await run_draft(client, headers, aid)
    other = await make_teacher(f"ai_o_{uuid.uuid4().hex[:8]}")
    assert (await client.get(f"{API}/ai/jobs/{job_id}", headers=other)).status_code == 404
    r = await client.post(f"{API}/ai/jobs/{job_id}/approve", headers=other, json={})
    assert r.status_code == 404
    r = await client.post(
        f"{API}/assignments/{aid}/ai/draft", headers=other, json={"source_text": SOURCE}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_approve_skips_duplicates_and_refuses_locked_criteria(
    client, assignment, ai_on, make_session, join
):
    headers, aid = assignment
    ai_on(FakeProvider(result=good_draft()))
    await client.post(
        f"{API}/assignments/{aid}/questions",
        headers=headers,
        json={"text": "Объясните пункт 0 работы подробно"},
    )
    job_id, job = await run_draft(client, headers, aid)
    r = await client.post(
        f"{API}/ai/jobs/{job_id}/approve",
        headers=headers,
        json={"questions": job["result"]["questions"][:2], "criteria": []},
    )
    assert r.json()["added_questions"] == 1 and r.json()["skipped_questions"] == 1

    await client.post(
        f"{API}/assignments/{aid}/criteria", headers=headers, json={"name": "Один", "max_points": 1}
    )
    code = await make_session(headers)
    await client.put(
        f"{API}/sessions/{code}/assignment", headers=headers, json={"assignment_id": aid}
    )
    await join(code, "Ai Student")
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    job2_id, job2 = await run_draft(client, headers, aid)
    r = await client.post(
        f"{API}/ai/jobs/{job2_id}/approve",
        headers=headers,
        json={"questions": [], "criteria": job2["result"]["criteria"]},
    )
    assert (r.status_code, r.json()["code"]) == (409, "criteria_locked")


@pytest.mark.asyncio
async def test_a_job_interrupted_by_a_restart_is_marked_as_error(client, assignment, ai_on):
    headers, aid = assignment
    ai_on(FakeProvider(result=good_draft(), delay=5))
    r = await client.post(
        f"{API}/assignments/{aid}/ai/draft", headers=headers, json={"source_text": SOURCE}
    )
    await asyncio.sleep(0.1)
    await runtime.shutdown()
    await runtime.sweep_interrupted()
    runtime.active = 0
    job = (await client.get(f"{API}/ai/jobs/{r.json()['id']}", headers=headers)).json()
    assert job["status"] == "error" and job["error_code"] == "ai_interrupted"


@pytest.mark.asyncio
async def test_ollama_provider_maps_transport_errors(monkeypatch):
    provider = OllamaProvider("http://ollama.invalid:1", "m", 1.0)

    class Boom(httpx.AsyncClient):
        async def post(self, *a, **k):
            raise self.exc

    def make(exc):
        class Client(Boom):
            pass

        Client.exc = exc
        return Client

    for exc, expected in [
        (httpx.ConnectError("x"), AIUnavailableError),
        (httpx.ReadTimeout("x"), AITimeoutError),
    ]:
        monkeypatch.setattr(httpx, "AsyncClient", make(exc))
        with pytest.raises(expected):
            await provider.generate_json(system="s", user="u", schema={}, max_tokens=10)


@pytest.mark.asyncio
async def test_ollama_provider_parses_a_chat_response(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"message": {"content": '{"questions": []}'}})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    provider = OllamaProvider("http://ollama:11434/", "qwen3.5:9b", 5.0)
    result = await provider.generate_json(
        system="S", user="U", schema={"type": "object"}, max_tokens=99
    )
    assert result == {"questions": []}
    assert seen["url"] == "http://ollama:11434/api/chat"
    assert '"think":false' in seen["body"].replace(" ", "") and "qwen3.5:9b" in seen["body"]

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(500)), **kw),
    )
    with pytest.raises(AIUnavailableError):
        await provider.generate_json(system="S", user="U", schema={}, max_tokens=9)


@pytest.mark.skipif(
    os.environ.get("RUN_OLLAMA_TESTS") != "1", reason="needs a running Ollama with the model"
)
@pytest.mark.asyncio
async def test_real_ollama_returns_a_valid_draft():
    provider = OllamaProvider(settings.OLLAMA_URL, settings.AI_MODEL, settings.AI_TIMEOUT_SECONDS)
    raw = await provider.generate_json(
        system=ai.DRAFT_SYSTEM,
        user=ai.draft_user_message(SOURCE),
        schema=ai.DRAFT_SCHEMA,
        max_tokens=settings.AI_MAX_OUTPUT_TOKENS,
    )
    draft = validate_draft(raw)
    assert len(draft.questions) >= 3
