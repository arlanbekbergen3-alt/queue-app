import json

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import SLOW, row
from tests.e2e.test_protocol_pages import login_as

pytestmark = pytest.mark.e2e


def reject(route, status: int, code: str, retry_after: str | None):
    headers = {"Content-Type": "application/json"}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    route.fulfill(
        status=status,
        headers=headers,
        body=json.dumps({"detail": "busy", "code": code, "request_id": "test"}),
    )


def fail_first(page: Page, pattern: str, status: int, code: str, retry_after: str | None, times=1):
    calls = {"count": 0}

    def handler(route):
        calls["count"] += 1
        if calls["count"] <= times:
            reject(route, status, code, retry_after)
        else:
            route.continue_()

    page.route(pattern, handler)
    return calls


def student_page(browser, live_server, code: str):
    context = browser.new_context(base_url=live_server)
    page = context.new_page()
    page.goto(f"/?code={code}")
    return context, page


def test_the_student_page_waits_for_retry_after_and_joins_on_its_own(
    browser, live_server, open_session
):
    teacher = open_session("Зачёт")
    ctx, page = student_page(browser, live_server, teacher["code"])
    calls = fail_first(page, "**/api/v1/sessions/*/join", 429, "rate_limited", "2")

    page.fill("#name", "Анна Иванова")
    page.click("#join-btn")
    expect(page.locator("#retry-note")).to_contain_text(
        "Сервер занят, повторяем через", timeout=SLOW
    )
    expect(page.locator("#status-badge")).to_have_text("В очереди", timeout=SLOW)
    expect(page.locator("#retry-note")).to_be_hidden()
    assert calls["count"] == 2
    ctx.close()


def test_the_student_page_gives_up_after_a_few_attempts_and_shows_the_wait(
    browser, live_server, open_session
):
    teacher = open_session("Зачёт")
    ctx, page = student_page(browser, live_server, teacher["code"])
    calls = fail_first(page, "**/api/v1/sessions/*/join", 503, "overloaded", "1", times=99)

    page.fill("#name", "Борис Орлов")
    page.click("#join-btn")
    expect(page.locator("#join-err")).to_contain_text("перегружен", timeout=30_000)
    expect(page.locator("#join-err")).to_contain_text("(через 1 с)")
    assert calls["count"] == 4
    ctx.close()


def test_a_long_wait_is_not_retried_but_is_explained(browser, live_server, open_session):
    teacher = open_session("Зачёт")
    ctx, page = student_page(browser, live_server, teacher["code"])
    calls = fail_first(page, "**/api/v1/sessions/*/join", 429, "rate_limited", "120", times=99)

    page.fill("#name", "Вера Лебедева")
    page.click("#join-btn")
    expect(page.locator("#join-err")).to_contain_text("Слишком много запросов", timeout=SLOW)
    expect(page.locator("#join-err")).to_contain_text("(через 2 мин)")
    assert calls["count"] == 1
    ctx.close()


def test_a_blocked_presence_code_is_never_retried_automatically(browser, live_server, open_session):
    teacher = open_session("Зачёт")
    ctx, page = student_page(browser, live_server, teacher["code"])
    calls = fail_first(page, "**/api/v1/sessions/*/join", 429, "presence_locked", "5", times=99)

    page.fill("#name", "Глеб Седов")
    page.click("#join-btn")
    expect(page.locator("#join-err")).to_contain_text("(через 5 с)", timeout=SLOW)
    assert calls["count"] == 1
    ctx.close()


def test_the_teacher_page_retries_a_busy_server_for_reads_and_actions(
    page: Page, browser, live_server, api, open_session
):
    teacher = open_session("Зачёт")
    login_as(page, teacher)
    read_calls = fail_first(page, "**/api/v1/sessions", 503, "overloaded", "1")
    page.goto("/teacher")
    expect(page.locator("#retry-note")).to_contain_text("повторяем через", timeout=SLOW)
    expect(page.get_by_role("button", name=teacher["code"])).to_be_visible(timeout=SLOW)
    assert read_calls["count"] >= 2

    page.get_by_role("button", name=teacher["code"]).click()
    ctx, student = student_page(browser, live_server, teacher["code"])
    student.fill("#name", "Дарья Белова")
    student.click("#join-btn")
    expect(row(page, "Дарья Белова")).to_be_visible(timeout=SLOW)
    action_calls = fail_first(page, "**/api/v1/sessions/*/next", 429, "rate_limited", "1")
    page.click("#next-btn")
    expect(page.locator("#work-who")).to_contain_text("Дарья Белова", timeout=SLOW)
    assert action_calls["count"] == 2
    ctx.close()


def test_an_action_that_failed_inside_the_server_is_not_repeated(
    page: Page, browser, live_server, open_session
):
    teacher = open_session("Зачёт")
    login_as(page, teacher)
    page.goto("/teacher")
    page.get_by_role("button", name=teacher["code"]).click()
    ctx, student = student_page(browser, live_server, teacher["code"])
    student.fill("#name", "Егор Волков")
    student.click("#join-btn")
    expect(row(page, "Егор Волков")).to_be_visible(timeout=SLOW)
    calls = fail_first(
        page, "**/api/v1/sessions/*/next", 503, "database_unavailable", "1", times=99
    )

    page.click("#next-btn")
    expect(page.locator("#action-err")).to_contain_text("недоступен", timeout=SLOW)
    assert calls["count"] == 1
    ctx.close()


def test_the_teacher_can_reopen_a_closed_session_and_the_grading_comes_back(
    page: Page, browser, live_server, api, open_session
):
    teacher = open_session("Зачёт")
    headers = teacher["headers"]
    made = api.post(
        "/assignments", headers=headers, json={"title": "Работа", "questions_per_submission": 1}
    ).json()
    api.post(
        f"/assignments/{made['id']}/questions",
        headers=headers,
        json={"text": "Вопрос", "difficulty": 1, "topic": "t"},
    )
    api.post(
        f"/assignments/{made['id']}/criteria",
        headers=headers,
        json={"name": "Ответ", "max_points": 5},
    )
    api.put(
        f"/sessions/{teacher['code']}/assignment",
        headers=headers,
        json={"assignment_id": made["id"]},
    )
    login_as(page, teacher)
    page.goto("/teacher")
    page.get_by_role("button", name=teacher["code"]).click()
    ctx, student = student_page(browser, live_server, teacher["code"])
    student.fill("#name", "Ирина Котова")
    student.click("#join-btn")
    expect(row(page, "Ирина Котова")).to_be_visible(timeout=SLOW)
    page.click("#next-btn")
    expect(page.locator("#work-who")).to_contain_text("Ирина Котова", timeout=SLOW)
    expect(page.locator("#work-body .crit")).to_have_count(1, timeout=SLOW)

    messages = []
    page.once("dialog", lambda dialog: (messages.append(dialog.message), dialog.accept()))
    page.click("#close-btn")
    expect(page.locator("#reopen-btn")).to_be_visible(timeout=SLOW)
    expect(page.locator("#close-btn")).to_be_hidden()
    assert "Идёт сдача" in messages[0] and "снова" in messages[0]

    page.click("#reopen-btn")
    expect(page.locator("#close-btn")).to_be_visible(timeout=SLOW)
    expect(page.locator("#reopen-btn")).to_be_hidden()
    expect(page.locator("#work-who")).to_contain_text("Ирина Котова", timeout=SLOW)
    ctx.close()
