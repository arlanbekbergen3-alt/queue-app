import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import SLOW, student_join
from tests.e2e.test_protocol_pages import login_as

pytestmark = pytest.mark.e2e


def make_timed_session(api, open_session, minutes, students, questions=3):
    teacher = open_session("Сдача с лимитом", duration_minutes=minutes)
    headers = teacher["headers"]
    assignment = api.post(
        "/assignments",
        headers=headers,
        json={"title": f"Работа {teacher['username']}", "questions_per_submission": questions},
    ).json()
    for i in range(6):
        api.post(
            f"/assignments/{assignment['id']}/questions",
            headers=headers,
            json={"text": f"Вопрос номер {i}", "difficulty": 2},
        )
    api.post(
        f"/assignments/{assignment['id']}/criteria",
        headers=headers,
        json={"name": "Код", "max_points": 5},
    )
    api.put(
        f"/sessions/{teacher['code']}/assignment",
        headers=headers,
        json={"assignment_id": assignment["id"]},
    )
    for i in range(students):
        api.post(
            f"/sessions/{teacher['code']}/join",
            json={"name": f"Студент {i}", "token": f"timed-{teacher['code']}-{i:03d}-xxxx"},
        )
    return teacher


def open_teacher(page: Page, teacher):
    login_as(page, teacher)
    page.goto("/teacher")
    page.get_by_role("button", name=teacher["code"]).click()


def test_forecast_panel_offers_fewer_questions_and_the_teacher_decides(
    page: Page, api, open_session, shot
):
    teacher = make_timed_session(api, open_session, minutes=30, students=8)
    open_teacher(page, teacher)
    expect(page.locator("#budget-panel")).to_be_visible(timeout=SLOW)
    expect(page.locator("#budget-status")).to_have_text("Не успеваем", timeout=SLOW)
    expect(page.locator("#budget-students")).to_have_text("8")
    expect(page.locator("#budget-each")).to_contain_text(re.compile(r"^3:\d\d"))
    expect(page.locator("#budget-end")).to_have_text(re.compile(r"^\d\d:\d\d$"))
    expect(page.locator("#budget-offer-text")).to_have_text(
        re.compile(r"^3 → 2 вопроса, закончим примерно в \d\d:\d\d$")
    )
    shot(page, "teacher-budget-over")

    page.click("#budget-decline")
    expect(page.locator("#budget-offer")).to_be_hidden(timeout=SLOW)
    expect(page.locator("#budget-status")).to_have_text("Не успеваем")

    page.reload()
    page.get_by_role("button", name=teacher["code"]).click()
    expect(page.locator("#budget-status")).to_have_text("Не успеваем", timeout=SLOW)
    expect(page.locator("#budget-offer")).to_be_hidden()
    page.click("#next-btn")
    expect(page.locator("#budget-students")).to_have_text("8", timeout=SLOW)


def test_accepting_the_offer_reduces_questions_and_offers_the_next_step(
    page: Page, api, open_session
):
    teacher = make_timed_session(api, open_session, minutes=30, students=8)
    open_teacher(page, teacher)
    expect(page.locator("#budget-offer-text")).to_contain_text("3 → 2 вопроса", timeout=SLOW)
    page.click("#budget-accept")
    expect(page.locator("#budget-offer-text")).to_contain_text("2 → 1 вопрос,", timeout=SLOW)
    page.click("#next-btn")
    expect(page.locator("#entries tr", has_text="вызван")).to_be_visible(timeout=SLOW)
    current = api.get(
        f"/sessions/{teacher['code']}/submissions/current", headers=teacher["headers"]
    ).json()
    assert len(current["questions"]) == 2
    page.click("#budget-accept")
    expect(page.locator("#budget-offer")).to_be_hidden(timeout=SLOW)


def test_the_panel_stays_hidden_without_a_time_limit(page: Page, open_session):
    teacher = open_session("Без лимита")
    open_teacher(page, teacher)
    expect(page.locator("#queue-card")).to_be_visible(timeout=SLOW)
    expect(page.locator("#budget-panel")).to_be_hidden()


def test_creating_a_session_with_a_time_limit_from_the_form(page: Page, api, open_session):
    teacher = open_session("Для входа")
    login_as(page, teacher)
    page.goto("/teacher")
    page.click("#w-more summary")
    page.fill("#title", "С лимитом из формы")
    page.fill("#limit-minutes", "45")
    page.click("#create-btn")
    expect(page.locator("#budget-panel")).to_be_visible(timeout=SLOW)
    expect(page.locator("#budget-left")).to_contain_text("мин")
    expect(page.locator("#budget-status")).to_have_text("Очередь пуста")

    page.fill("#limit-until", "23:59")
    page.fill("#limit-minutes", "10")
    page.fill("#title", "Оба поля")
    page.click("#create-btn")
    expect(page.locator("#create-err")).to_have_text(
        "Укажите либо время окончания, либо число минут", timeout=SLOW
    )


def test_student_sees_an_approximate_call_time(page: Page, api, open_session):
    teacher = make_timed_session(api, open_session, minutes=60, students=2)
    student_join(page, teacher["code"], "Третий Студент")
    expect(page.locator("#eta")).to_have_text(
        re.compile(r"≈ 10 мин \(около \d\d:\d\d\)"), timeout=SLOW
    )
    api.post(f"/sessions/{teacher['code']}/next", headers=teacher["headers"])
    first = api.get(f"/sessions/{teacher['code']}/queue", headers=teacher["headers"]).json()[
        "entries"
    ][0]
    api.post(f"/sessions/{teacher['code']}/entries/{first['id']}/skip", headers=teacher["headers"])
    expect(page.locator("#eta")).to_have_text(
        re.compile(r"≈ 5 мин \(около \d\d:\d\d\)"), timeout=SLOW
    )


def test_timer_turns_yellow_then_red_when_the_budget_is_exceeded_and_nothing_stops(
    page: Page, api, open_session
):
    teacher = make_timed_session(api, open_session, minutes=1, students=1)
    api.post(f"/sessions/{teacher['code']}/next", headers=teacher["headers"])
    page.clock.install()
    login_as(page, teacher)
    page.goto(f"/protocol?code={teacher['code']}")
    timer = page.locator("#current .timer")
    expect(timer).to_be_visible(timeout=SLOW)
    expect(timer).not_to_have_class(re.compile(r"warn|bad"))
    page.clock.fast_forward(60_000)
    expect(timer).to_have_class(re.compile(r"warn"), timeout=SLOW)
    page.clock.fast_forward(60_000)
    expect(timer).to_have_class(re.compile(r"bad"), timeout=SLOW)
    expect(page.get_by_role("button", name="Завершить сдачу")).to_be_enabled()
