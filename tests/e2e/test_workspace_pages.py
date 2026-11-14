import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import SLOW, row, student_join, teacher_login
from tests.e2e.test_protocol_pages import login_as, prepare

pytestmark = pytest.mark.e2e


def open_teacher_page(page: Page, teacher: dict) -> None:
    login_as(page, teacher)
    page.goto("/teacher")
    expect(page.locator("#app-view")).to_be_visible(timeout=SLOW)


def started_code(page: Page) -> str:
    expect(page.locator("#start-code")).to_have_text(re.compile(r"^[A-Z2-9]{6}$"), timeout=SLOW)
    return page.locator("#start-code").inner_text()


def new_context(browser, live_server):
    context = browser.new_context(base_url=live_server)
    return context, context.new_page()


def test_starting_a_lesson_takes_one_click_and_shows_code_link_and_qr(
    page: Page, open_session, shot
):
    teacher = open_session("Старый")
    open_teacher_page(page, teacher)
    expect(page.locator("#mode")).to_have_value("protocol")
    page.click("#create-btn")
    expect(page.locator("#start-panel")).to_be_visible(timeout=SLOW)
    code = started_code(page)
    assert len(code) == 6 and code.isalnum()
    expect(page.locator("#start-link")).to_have_text(
        f"{page.url.split('/teacher')[0]}/?code={code}"
    )
    qr = page.locator("#start-qr")
    expect(qr).to_be_visible()
    page.wait_for_function("document.getElementById('start-qr').naturalWidth > 0")
    box = qr.bounding_box()
    assert box and box["width"] > 200
    shot(page, "teacher-start-panel")

    page.click("#projector-btn")
    expect(page.locator("#projector")).to_be_visible()
    expect(page.locator("#projector-code")).to_have_text(code)
    page.wait_for_function("document.getElementById('projector-qr').naturalWidth > 0")
    page.keyboard.press("Escape")
    expect(page.locator("#projector")).to_be_hidden()
    page.click("#start-hide")
    expect(page.locator("#start-panel")).to_be_hidden()
    page.click("#start-show")
    expect(page.locator("#start-panel")).to_be_visible()


def test_the_form_starts_from_the_last_session_settings(page: Page, api, open_session):
    teacher = open_session(
        "Прошлое занятие",
        group="ИС-22",
        session_mode="quick",
        quick_max_points=20,
        duration_minutes=45,
        order_mode="lottery",
        ready_priority=True,
        ready_bonus_points=2,
    )
    assert teacher["code"]
    open_teacher_page(page, teacher)
    expect(page.locator("#group")).to_have_value("ИС-22", timeout=SLOW)
    expect(page.locator("#mode")).to_have_value("quick")
    expect(page.locator("#quick-max")).to_have_value("20")
    expect(page.locator("#limit-minutes")).to_have_value("45")
    expect(page.locator("#order-mode")).to_have_value("lottery")
    expect(page.locator("#w-quick")).to_be_visible()
    expect(page.locator("#w-protocol")).to_be_hidden()
    page.click("#w-more summary")
    expect(page.locator("#ready-priority")).to_be_checked()
    expect(page.locator("#ready-bonus")).to_have_value("2")


def test_empty_states_tell_what_to_do(page: Page, browser, live_server, open_session):
    teacher = open_session("Пустая")
    open_teacher_page(page, teacher)
    page.get_by_role("button", name=teacher["code"]).click()
    expect(page.locator("#entries")).to_contain_text(
        "Очередь пуста. Покажите код студентам", timeout=SLOW
    )
    expect(page.locator("#work-card")).to_be_hidden()


def test_quick_grade_takes_three_key_presses_from_the_call_to_the_result(
    page: Page, browser, live_server, open_session
):
    teacher = open_session("Основа")
    open_teacher_page(page, teacher)
    page.select_option("#mode", "quick")
    expect(page.locator("#w-quick")).to_be_visible()
    page.click("#create-btn")
    code = started_code(page)
    ctx, student = new_context(browser, live_server)
    student_join(student, code, "Анна Иванова")
    expect(row(page, "Анна Иванова")).to_be_visible(timeout=SLOW)
    expect(page.locator("#work-card")).to_be_hidden()

    page.keyboard.press("n")
    expect(page.locator("#work-card")).to_be_visible(timeout=SLOW)
    expect(page.locator("#work-who")).to_contain_text("Анна Иванова")
    expect(page.locator("#work-body .crit")).to_have_count(1)
    expect(page.locator("#work-body .pts button")).to_have_count(11)
    page.keyboard.press("8")
    expect(page.locator("#work-body .pts button.on")).to_have_text("8")
    page.keyboard.press("Enter")
    expect(page.locator("#work-card")).to_be_hidden(timeout=SLOW)
    expect(student.locator("#result")).to_contain_text("Итог: 8 из 10", timeout=SLOW)
    expect(student.locator("#result")).to_contain_text("Оценка")
    ctx.close()


def test_quick_grade_by_buttons_and_a_tag_note(page: Page, browser, live_server, open_session):
    teacher = open_session("Основа 2")
    open_teacher_page(page, teacher)
    page.select_option("#mode", "quick")
    page.fill("#quick-max", "5")
    page.click("#create-btn")
    code = started_code(page)
    ctx, student = new_context(browser, live_server)
    student_join(student, code, "Борис Петров")
    expect(row(page, "Борис Петров")).to_be_visible(timeout=SLOW)
    page.click("#next-btn")
    expect(page.locator("#work-card")).to_be_visible(timeout=SLOW)
    page.locator("#work-body .pts button", has_text="4").click()
    page.get_by_role("button", name="Есть ошибки").click()
    page.get_by_role("button", name="Нужна пересдача").click()
    expect(page.locator("#work-body textarea")).to_have_value("Есть ошибки; Нужна пересдача")
    page.get_by_role("button", name="Есть ошибки").click()
    expect(page.locator("#work-body textarea")).to_have_value("Нужна пересдача")
    page.get_by_role("button", name="Завершить сдачу").click()
    expect(student.locator("#result")).to_contain_text("Итог: 4 из 5", timeout=SLOW)
    expect(student.locator("#result")).to_contain_text("Нужна пересдача")
    ctx.close()


def test_finishing_without_a_score_is_refused_with_a_clear_message(
    page: Page, browser, live_server, open_session
):
    teacher = open_session("Основа 3")
    open_teacher_page(page, teacher)
    page.select_option("#mode", "quick")
    page.click("#create-btn")
    code = started_code(page)
    ctx, student = new_context(browser, live_server)
    student_join(student, code, "Вера Смирнова")
    expect(row(page, "Вера Смирнова")).to_be_visible(timeout=SLOW)
    page.click("#next-btn")
    expect(page.locator("#work-card")).to_be_visible(timeout=SLOW)
    page.get_by_role("button", name="Завершить сдачу").click()
    expect(page.locator("#work-err")).to_have_text("Выставьте баллы по всем критериям")
    expect(page.locator("#work-card")).to_be_visible()
    ctx.close()


def test_queue_only_mode_has_no_grades_just_done_and_skip(
    page: Page, browser, live_server, open_session
):
    teacher = open_session("Без оценок")
    open_teacher_page(page, teacher)
    page.select_option("#mode", "queue_only")
    expect(page.locator("#w-protocol")).to_be_hidden()
    page.click("#create-btn")
    code = started_code(page)
    ctx, student = new_context(browser, live_server)
    student_join(student, code, "Глеб Орлов")
    expect(row(page, "Глеб Орлов")).to_be_visible(timeout=SLOW)
    page.keyboard.press("n")
    expect(page.locator("#work-card")).to_be_visible(timeout=SLOW)
    expect(page.locator("#work-body .crit")).to_have_count(0)
    expect(page.locator("#work-body")).to_contain_text("Режим без оценок")
    page.keyboard.press("Enter")
    expect(page.locator("#work-card")).to_be_hidden(timeout=SLOW)
    expect(student.locator("#message")).to_contain_text("Сдача завершена", timeout=SLOW)
    ctx.close()


def test_protocol_mode_grades_in_place_with_hotkeys_and_max_button(
    page: Page, browser, live_server, api, open_session
):
    teacher, assignment = prepare(api, open_session, questions=4)
    open_teacher_page(page, teacher)
    page.get_by_role("button", name=teacher["code"]).click()
    ctx, student = new_context(browser, live_server)
    student_join(student, teacher["code"], "Дарья Белова")
    expect(row(page, "Дарья Белова")).to_be_visible(timeout=SLOW)
    page.keyboard.press("n")
    expect(page.locator("#work-card")).to_be_visible(timeout=SLOW)
    expect(page.locator("#work-body li")).to_have_count(2)
    expect(page.locator("#work-body .crit")).to_have_count(2)
    expect(page.locator("#work-body .crit.active")).to_contain_text("Код")
    page.keyboard.press("4")
    expect(page.locator("#work-body .crit.active")).to_contain_text("Защита")
    page.keyboard.press("5")
    page.keyboard.press("Enter")
    expect(student.locator("#result")).to_contain_text("Итог: 9 из 10", timeout=SLOW)

    student2_ctx, student2 = new_context(browser, live_server)
    student_join(student2, teacher["code"], "Егор Волков")
    expect(row(page, "Егор Волков")).to_be_visible(timeout=SLOW)
    page.click("#next-btn")
    expect(page.locator("#work-who")).to_contain_text("Егор Волков", timeout=SLOW)
    page.click("#work-max")
    expect(page.locator("#work-body .pts button.on")).to_have_count(2)
    page.get_by_role("button", name="Отлично").click()
    page.click("#work-finish")
    expect(student2.locator("#result")).to_contain_text("Итог: 10 из 10", timeout=SLOW)
    expect(student2.locator("#result")).to_contain_text("Отлично")
    ctx.close()
    student2_ctx.close()


def test_hotkeys_are_ignored_while_typing_and_help_opens_with_a_question_mark(
    page: Page, browser, live_server, api, open_session
):
    teacher, _ = prepare(api, open_session, questions=4)
    open_teacher_page(page, teacher)
    page.get_by_role("button", name=teacher["code"]).click()
    ctx, student = new_context(browser, live_server)
    student_join(student, teacher["code"], "Жанна Крылова")
    expect(row(page, "Жанна Крылова")).to_be_visible(timeout=SLOW)
    page.keyboard.press("?")
    expect(page.locator("#help-overlay")).to_be_visible()
    expect(page.locator("#help-overlay")).to_contain_text("Горячие клавиши")
    page.keyboard.press("Escape")
    expect(page.locator("#help-overlay")).to_be_hidden()
    page.keyboard.press("n")
    expect(page.locator("#work-card")).to_be_visible(timeout=SLOW)
    page.locator("#work-body textarea").click()
    page.keyboard.type("n5?")
    expect(page.locator("#work-body textarea")).to_have_value("n5?")
    expect(page.locator("#work-body .pts button.on")).to_have_count(0)
    expect(page.locator("#help-overlay")).to_be_hidden()
    page.locator("#work-body textarea").blur()
    page.keyboard.press("Enter")
    expect(page.locator("#work-err")).to_have_text("Выставьте баллы по всем критериям")
    ctx.close()


def test_teacher_login_form_still_works_and_lists_sessions_with_a_hint(page: Page, open_session):
    teacher = open_session("Вход")
    teacher_login(page, teacher["username"], teacher["password"])
    expect(page.locator("#sessions")).to_contain_text("Вход · ", timeout=SLOW)


def test_level_descriptions_appear_under_the_buttons_and_the_chosen_one_is_marked(
    page: Page, browser, live_server, api, open_session
):
    teacher, assignment = prepare(api, open_session, questions=4)
    headers = teacher["headers"]
    first = api.get(f"/assignments/{assignment['id']}/criteria", headers=headers).json()[0]
    api.put(
        f"/assignments/{assignment['id']}/criteria/{first['id']}/levels",
        headers=headers,
        json={"levels": [{"points": 0, "text": "Нет ответа"}, {"points": 4, "text": "Почти всё"}]},
    )
    open_teacher_page(page, teacher)
    page.get_by_role("button", name=teacher["code"]).click()
    ctx, student = new_context(browser, live_server)
    student_join(student, teacher["code"], "Лиза Орлова")
    expect(row(page, "Лиза Орлова")).to_be_visible(timeout=SLOW)
    page.keyboard.press("n")
    expect(page.locator("#work-card")).to_be_visible(timeout=SLOW)
    legend = page.locator("#work-body .crit").first.locator(".lvl")
    expect(legend).to_contain_text("0 — Нет ответа")
    expect(legend).to_contain_text("4 — Почти всё")
    expect(page.locator("#work-body .crit").nth(1).locator(".lvl")).to_have_count(0)
    page.keyboard.press("4")
    expect(legend.locator("span.on")).to_have_text("4 — Почти всё")
    expect(
        page.locator("#work-body .crit").first.locator("button[data-points='4']")
    ).to_have_attribute("title", "Почти всё")
    ctx.close()
