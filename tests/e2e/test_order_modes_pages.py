import pytest
from playwright.sync_api import expect

from tests.e2e.test_pages import SLOW, order, row, teacher_login
from tests.e2e.test_protocol_pages import login_as

pytestmark = pytest.mark.e2e


def new_student(browser, live_server, code: str, name: str, **before):
    context = browser.new_context(base_url=live_server)
    page = context.new_page()
    page.goto("/")
    page.fill("#code", code)
    page.fill("#name", name)
    if before.get("ready"):
        expect(page.locator("#ready-choice")).to_be_visible(timeout=SLOW)
        page.check("#ready-now")
    page.click("#join-btn")
    return context, page


def teacher_page(browser, live_server, session):
    context = browser.new_context(base_url=live_server)
    page = context.new_page()
    teacher_login(page, session["username"], session["password"])
    page.get_by_role("button", name=session["code"]).click()
    return context, page


def test_lottery_hides_numbers_until_the_teacher_closes_registration_and_shuffles(
    browser, live_server, open_session, shot
):
    session = open_session(order_mode="lottery")
    code = session["code"]
    people = [new_student(browser, live_server, code, f"Студент {n}") for n in "АБВ"]
    for _, page in people:
        expect(page.locator("#queue-view")).to_be_visible(timeout=SLOW)
        expect(page.locator("#message")).to_contain_text("Вы в списке. Порядок определится")
        expect(page.locator("#ticket")).to_have_text("—")
    expect(people[0][1].locator("#message")).to_contain_text("Записалось: 3", timeout=SLOW)
    shot(people[0][1], "student-lottery-pool")

    teacher_ctx, teacher = teacher_page(browser, live_server, session)
    expect(row(teacher, "Студент А")).to_be_visible(timeout=SLOW)
    expect(teacher.locator("#queue-modes")).to_contain_text("жеребьёвка после закрытия записи")
    expect(teacher.locator("#next-btn")).to_be_disabled()
    expect(teacher.locator("#lottery-btn")).to_be_visible()

    teacher.once("dialog", lambda dialog: dialog.accept())
    teacher.click("#lottery-btn")
    expect(teacher.locator("#lottery-btn")).to_be_hidden(timeout=SLOW)
    expect(teacher.locator("#registration-btn")).to_have_text("Открыть запись")
    expect(teacher.locator("#next-btn")).to_be_enabled(timeout=SLOW)
    shown = sorted(c.split("\t")[0] for c in teacher.locator("#entries tr").all_inner_texts())
    assert shown == ["1", "2", "3"]

    tickets = []
    for _, page in people:
        expect(page.locator("#ticket")).to_contain_text("№", timeout=SLOW)
        tickets.append(page.locator("#ticket").inner_text())
    assert sorted(tickets) == ["№ 1", "№ 2", "№ 3"]
    expect(people[0][1].locator("#message")).not_to_contain_text("Вы в списке")

    teacher.click("#lottery-log-btn")
    expect(teacher.locator("#lottery-log")).to_contain_text("участников: 3", timeout=SLOW)
    expect(teacher.locator("#lottery-log")).to_contain_text("Студент")
    shot(teacher, "teacher-lottery-done")

    first = order(teacher)[0]
    teacher.click("#next-btn")
    expect(row(teacher, first)).to_contain_text("вызван", timeout=SLOW)
    for context, _ in people:
        context.close()
    teacher_ctx.close()


def test_ready_students_go_first_and_perks_are_visible_everywhere(
    browser, live_server, open_session, shot
):
    session = open_session(ready_priority=True, ready_bonus_points=2, ready_skip_questions=1)
    code = session["code"]
    first_ctx, first = new_student(browser, live_server, code, "Первая Спокойная")
    second_ctx, second = new_student(browser, live_server, code, "Вторая Готовая", ready=True)
    expect(second.locator("#queue-view")).to_be_visible(timeout=SLOW)
    expect(second.locator("#position")).to_have_text("1", timeout=SLOW)
    expect(second.locator("#ready-state")).to_have_text("Готов сдать сразу")
    expect(second.locator("#perks-note")).to_contain_text("бонус за раннюю сдачу: +2")
    expect(second.locator("#perks-note")).to_contain_text("на один вопрос меньше")
    expect(first.locator("#position")).to_have_text("2", timeout=SLOW)
    expect(first.locator("#readynow-btn")).to_have_text("Готов сдать сразу")
    shot(second, "student-ready-priority")

    teacher_ctx, teacher = teacher_page(browser, live_server, session)
    expect(row(teacher, "Вторая Готовая")).to_contain_text("готов сразу", timeout=SLOW)
    assert order(teacher) == ["Вторая Готовая", "Первая Спокойная"]
    expect(teacher.locator("#queue-modes")).to_contain_text("бонус +2")

    first.click("#readynow-btn")
    expect(first.locator("#ready-state")).to_have_text("Готов сдать сразу", timeout=SLOW)
    expect(first.locator("#position")).to_have_text("1", timeout=SLOW)
    expect(second.locator("#position")).to_have_text("2", timeout=SLOW)
    assert order(teacher) == ["Первая Спокойная", "Вторая Готовая"]
    second.click("#readynow-btn")
    expect(second.locator("#ready-state")).to_have_text("Ещё готовлюсь", timeout=SLOW)
    expect(second.locator("#position")).to_have_text("2", timeout=SLOW)

    teacher.click("#registration-btn")
    expect(teacher.locator("#registration-btn")).to_have_text("Открыть запись", timeout=SLOW)
    expect(first.locator("#readynow-btn")).to_be_hidden(timeout=SLOW)
    for context in (first_ctx, second_ctx, teacher_ctx):
        context.close()


def test_teacher_changes_the_order_settings_before_registration_closes(
    browser, live_server, open_session
):
    session = open_session()
    student_ctx, student = new_student(browser, live_server, session["code"], "Анна Иванова")
    expect(student.locator("#queue-view")).to_be_visible(timeout=SLOW)
    expect(student.locator("#priority-box")).to_be_hidden()
    teacher_ctx, teacher = teacher_page(browser, live_server, session)
    expect(teacher.locator("#queue-modes")).to_contain_text("по порядку записи", timeout=SLOW)
    teacher.click("#settings-box summary")
    teacher.select_option("#s-order-mode", "lottery")
    teacher.check("#s-ready-priority")
    teacher.select_option("#s-ready-bonus", "3")
    teacher.click("#settings-save")
    expect(teacher.locator("#queue-modes")).to_contain_text("бонус +3", timeout=SLOW)
    expect(teacher.locator("#lottery-btn")).to_be_visible()
    expect(student.locator("#priority-box")).to_be_visible(timeout=SLOW)
    expect(student.locator("#message")).to_contain_text("Вы в списке", timeout=SLOW)
    teacher.click("#registration-btn")
    expect(teacher.locator("#settings-box")).to_be_hidden(timeout=SLOW)
    student_ctx.close()
    teacher_ctx.close()


def test_bonus_appears_for_the_student_and_the_teacher_removes_it(
    browser, live_server, api, open_session
):
    session = open_session(ready_priority=True, ready_bonus_points=2)
    headers = session["headers"]
    code = session["code"]
    assignment = api.post(
        "/assignments",
        headers=headers,
        json={"title": "Работа бонус", "questions_per_submission": 1},
    ).json()
    api.post(
        f"/assignments/{assignment['id']}/questions",
        headers=headers,
        json={"text": "Вопрос", "difficulty": 1, "topic": "t"},
    )
    criterion = api.post(
        f"/assignments/{assignment['id']}/criteria",
        headers=headers,
        json={"name": "Код", "max_points": 5},
    ).json()
    api.put(
        f"/sessions/{code}/assignment", headers=headers, json={"assignment_id": assignment["id"]}
    )
    student_ctx, student = new_student(browser, live_server, code, "Анна Иванова", ready=True)
    expect(student.locator("#queue-view")).to_be_visible(timeout=SLOW)
    api.post(f"/sessions/{code}/next", headers=headers)
    current = api.get(f"/sessions/{code}/submissions/current", headers=headers).json()
    api.post(
        f"/submissions/{current['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": 4}], "comment": ""},
    )
    expect(student.locator("#bonus-line")).to_have_text("+2", timeout=SLOW)
    expect(student.locator("#result")).to_contain_text("Бонус за раннюю сдачу")
    expect(student.locator("#result")).to_contain_text("Итог: 4 из 5")

    teacher_ctx = browser.new_context(base_url=live_server)
    teacher = teacher_ctx.new_page()
    login_as(teacher, session)
    teacher.goto(f"/protocol?code={code}")
    expect(teacher.locator("#done")).to_contain_text("бонус +2", timeout=SLOW)
    teacher.once("dialog", lambda dialog: dialog.accept("Списал"))
    teacher.get_by_role("button", name="Снять бонус").click()
    expect(teacher.locator("#done")).not_to_contain_text("бонус +2", timeout=SLOW)
    teacher.get_by_role("button", name="Журнал").click()
    expect(teacher.locator("#audit")).to_contain_text("Списал", timeout=SLOW)
    student_ctx.close()
    teacher_ctx.close()
