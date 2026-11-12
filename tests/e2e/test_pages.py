import re

import pytest
from playwright.sync_api import Page, expect

from core.config import settings

pytestmark = pytest.mark.e2e

SLOW = 20_000


def student_join(page: Page, code: str, name: str) -> None:
    page.goto("/")
    page.fill("#code", code)
    page.fill("#name", name)
    page.click("#join-btn")


def teacher_login(page: Page, username: str, password: str) -> None:
    page.goto("/teacher")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#auth-btn")
    expect(page.locator("#app-view")).to_be_visible()


def row(page: Page, name: str):
    return page.locator("#entries tr", has_text=name)


def order(page: Page) -> list[str]:
    return [cells.split("\t")[1] for cells in page.locator("#entries tr").all_inner_texts()]


def test_student_joins_by_code_and_sees_position(page: Page, open_session, shot):
    session = open_session()
    student_join(page, session["code"], "Анна Иванова")

    expect(page.locator("#queue-view")).to_be_visible()
    expect(page.locator("#position")).to_have_text("1")
    expect(page.locator("#ticket")).to_have_text("№ 1")
    expect(page.locator("#status-badge")).to_have_text("В очереди")
    shot(page, "student-waiting")


def test_student_link_with_code_prefills_the_form(page: Page, open_session):
    session = open_session()
    page.goto(f"/?code={session['code'].lower()}")

    expect(page.locator("#code")).to_have_value(session["code"])


def test_teacher_registers_creates_session_and_student_appears(
    page: Page, browser, live_server, shot
):
    page.goto("/teacher")
    page.click("#tab-register")
    page.fill("#username", "ui_teacher_register")
    page.fill("#password", "password123")
    page.fill("#invite", settings.TEACHER_INVITE_CODE)
    page.click("#auth-btn")
    expect(page.locator("#app-view")).to_be_visible()

    page.fill("#title", "Практика 5, группа ИС-22")
    page.click("#create-btn")
    code = page.locator("#queue-code")
    expect(code).to_have_text(re.compile(r"^[A-Z2-9]{6}$"))
    expect(page.locator("#entries")).to_contain_text("Очередь пуста")
    shot(page, "teacher-empty-queue")

    student = browser.new_context(base_url=live_server)
    student_join(student.new_page(), code.inner_text(), "Борис Петров")
    expect(row(page, "Борис Петров")).to_be_visible(timeout=SLOW)
    student.close()


def test_teacher_wrong_invite_code_is_rejected(page: Page):
    page.goto("/teacher")
    page.click("#tab-register")
    page.fill("#username", "ui_bad_invite")
    page.fill("#password", "password123")
    page.fill("#invite", "definitely-wrong")
    page.click("#auth-btn")

    expect(page.locator("#auth-err")).to_have_text("Неверный код приглашения")
    expect(page.locator("#app-view")).to_be_hidden()


def test_teacher_registration_validation_error_names_the_field_and_the_rule(page: Page):
    page.goto("/teacher")
    page.click("#tab-register")
    page.fill("#username", "anna@example.com")
    page.fill("#password", "short")
    page.fill("#invite", settings.TEACHER_INVITE_CODE)
    page.click("#auth-btn")

    error = page.locator("#auth-err")
    expect(error).to_contain_text("Логин: только латинские буквы")
    expect(error).to_contain_text("Пароль: минимум 8 симв.")
    expect(error).not_to_have_text("Проверьте введённые данные")
    expect(page.locator("#app-view")).to_be_hidden()


def test_teacher_wrong_password_shows_an_error(page: Page, open_session):
    session = open_session()
    page.goto("/teacher")
    page.fill("#username", session["username"])
    page.fill("#password", "wrong-password")
    page.click("#auth-btn")

    expect(page.locator("#auth-err")).to_have_text("Неверный логин или пароль")
    expect(page.locator("#app-view")).to_be_hidden()


def test_teacher_full_flow_next_skip_requeue_promote_close(
    page: Page, browser, live_server, open_session, shot
):
    session = open_session("Сдача ЛР")
    contexts = []
    for name in ("Анна", "Борис", "Клара"):
        context = browser.new_context(base_url=live_server)
        contexts.append(context)
        student_join(context.new_page(), session["code"], name)

    teacher_login(page, session["username"], session["password"])
    page.click("#sessions button")
    expect(row(page, "Клара")).to_be_visible(timeout=SLOW)
    shot(page, "teacher-queue")

    page.click("#next-btn")
    expect(row(page, "Анна")).to_contain_text("вызван")
    shot(page, "teacher-called")

    row(page, "Анна").get_by_text("Пропустить").click()
    expect(row(page, "Анна")).to_contain_text("пропущен")

    row(page, "Клара").get_by_text("Вперёд").click()
    expect(page.locator("#entries tr").first).to_contain_text("Клара")

    row(page, "Анна").get_by_text("Вернуть").click()
    expect(row(page, "Анна")).to_contain_text("ждёт")
    assert order(page) == ["Клара", "Борис", "Анна"]

    page.once("dialog", lambda dialog: dialog.accept())
    page.click("#close-btn")
    expect(page.locator("#queue-title")).to_contain_text("(закрыта)")
    expect(page.locator("#close-btn")).to_be_hidden()
    shot(page, "teacher-closed")
    for context in contexts:
        context.close()


def test_two_students_in_two_browser_contexts_see_their_own_places(
    page: Page, browser, live_server, open_session, shot
):
    session = open_session()
    first_ctx = browser.new_context(base_url=live_server)
    second_ctx = browser.new_context(base_url=live_server)
    first, second = first_ctx.new_page(), second_ctx.new_page()
    student_join(first, session["code"], "Первый")
    student_join(second, session["code"], "Второй")

    expect(first.locator("#position")).to_have_text("1")
    expect(second.locator("#position")).to_have_text("2")
    expect(second.locator("#ticket")).to_have_text("№ 2")

    teacher_login(page, session["username"], session["password"])
    page.click("#sessions button")
    expect(row(page, "Второй")).to_be_visible(timeout=SLOW)
    page.click("#next-btn")

    expect(first.locator("#status-badge")).to_have_text("Вас вызвали", timeout=SLOW)
    expect(first.locator("#message")).to_contain_text("Подойдите к преподавателю")
    expect(second.locator("#now-serving")).to_contain_text("Первый", timeout=SLOW)
    shot(first, "student-called")

    row(page, "Первый").get_by_text("Сдал").click()
    expect(first.locator("#status-badge")).to_have_text("Сдано", timeout=SLOW)
    expect(second.locator("#message")).to_contain_text("Вы следующий", timeout=SLOW)
    shot(second, "student-next")
    first_ctx.close()
    second_ctx.close()


def test_student_leaves_the_queue_and_can_join_again(page: Page, open_session):
    session = open_session()
    student_join(page, session["code"], "Уходящий")
    expect(page.locator("#status-badge")).to_have_text("В очереди")

    page.click("#leave-btn")
    expect(page.locator("#status-badge")).to_have_text("Вы вышли")
    page.click("#again-btn")
    expect(page.locator("#join-view")).to_be_visible()
    expect(page.locator("#code")).to_have_value(session["code"])


def test_reload_keeps_the_entry_via_local_storage(page: Page, api, open_session):
    session = open_session()
    student_join(page, session["code"], "Не теряюсь")
    expect(page.locator("#ticket")).to_have_text("№ 1")

    page.reload()

    expect(page.locator("#queue-view")).to_be_visible()
    expect(page.locator("#ticket")).to_have_text("№ 1")
    expect(page.locator("#position")).to_have_text("1")
    queue = api.get(f"/sessions/{session['code']}/queue", headers=session["headers"]).json()
    assert [e["name"] for e in queue["entries"]] == ["Не теряюсь"]


def test_wrong_session_code_shows_an_error(page: Page):
    student_join(page, "ZZZZZZ", "Заблудившийся")

    expect(page.locator("#join-err")).to_have_text("Сессия с таким кодом не найдена")
    expect(page.locator("#queue-view")).to_be_hidden()


def test_empty_fields_are_rejected_without_a_request(page: Page):
    page.goto("/")
    page.click("#join-btn")

    expect(page.locator("#join-err")).to_have_text("Введите код и имя")


def test_closed_session_rejects_new_students_with_a_readable_message(
    page: Page, api, open_session, shot
):
    session = open_session()
    api.post(f"/sessions/{session['code']}/close", headers=session["headers"])

    student_join(page, session["code"], "Опоздавший")

    expect(page.locator("#join-err")).to_have_text("Сессия закрыта")
    shot(page, "student-closed-session")


def test_waiting_student_sees_that_the_session_was_closed(page: Page, api, open_session):
    session = open_session()
    student_join(page, session["code"], "Ждущий")
    expect(page.locator("#status-badge")).to_have_text("В очереди")

    api.post(f"/sessions/{session['code']}/close", headers=session["headers"])
    page.reload()

    expect(page.locator("#message")).to_contain_text("Сессия закрыта преподавателем")


def test_names_are_not_rendered_as_html(page: Page, browser, live_server, open_session):
    session = open_session()
    student_join(page, session["code"], "<img src=x onerror=window.__xss=1>")
    expect(page.locator("#queue-view")).to_be_visible()
    assert page.evaluate("window.__xss") is None

    teacher = browser.new_context(base_url=live_server)
    teacher_page = teacher.new_page()
    teacher_login(teacher_page, session["username"], session["password"])
    teacher_page.click("#sessions button")
    expect(teacher_page.locator("#entries")).to_contain_text("<img src=x", timeout=SLOW)
    assert teacher_page.evaluate("window.__xss") is None
    teacher.close()


def test_student_page_updates_in_real_time_without_waiting_for_a_poll(
    page: Page, api, open_session
):
    session = open_session()
    for name in ("Первый", "Второй"):
        api.post(
            f"/sessions/{session['code']}/join", json={"name": name, "token": name.ljust(16, "x")}
        )
    student_join(page, session["code"], "Третий")
    expect(page.locator("#position")).to_have_text("3")
    page.wait_for_function("() => liveConnected === true", timeout=SLOW)

    api.post(f"/sessions/{session['code']}/next", headers=session["headers"])

    expect(page.locator("#position")).to_have_text("2", timeout=2500)
    expect(page.locator("#now-serving")).to_contain_text("Первый", timeout=2500)


def test_student_page_falls_back_to_polling_when_the_stream_is_blocked(
    page: Page, api, open_session
):
    session = open_session()
    api.post(f"/sessions/{session['code']}/join", json={"name": "Первый", "token": "p" * 16})
    page.route("**/events", lambda route: route.abort())
    student_join(page, session["code"], "Второй")
    expect(page.locator("#position")).to_have_text("2")

    api.post(f"/sessions/{session['code']}/next", headers=session["headers"])

    expect(page.locator("#position")).to_have_text("1", timeout=SLOW)


def test_teacher_page_updates_in_real_time_when_a_student_joins(page: Page, api, open_session):
    session = open_session()
    teacher_login(page, session["username"], session["password"])
    page.click("#sessions button")
    expect(page.locator("#entries")).to_contain_text("Очередь пуста")
    page.wait_for_function("() => liveConnected === true", timeout=SLOW)

    api.post(f"/sessions/{session['code']}/join", json={"name": "Новичок", "token": "n" * 16})

    expect(row(page, "Новичок")).to_be_visible(timeout=2000)


def test_teacher_page_falls_back_to_polling_when_the_stream_is_blocked(
    page: Page, api, open_session
):
    session = open_session()
    page.route("**/queue/events", lambda route: route.abort())
    teacher_login(page, session["username"], session["password"])
    page.click("#sessions button")
    expect(page.locator("#entries")).to_contain_text("Очередь пуста")

    api.post(f"/sessions/{session['code']}/join", json={"name": "Новичок", "token": "n" * 16})

    expect(row(page, "Новичок")).to_be_visible(timeout=8000)


def test_group_is_shown_to_teacher_and_student(page: Page, browser, live_server, open_session):
    session = open_session("Лабораторная 7", group="ИС-22")
    teacher_login(page, session["username"], session["password"])
    expect(page.locator("#sessions")).to_contain_text("Лабораторная 7 · ИС-22")
    page.click("#sessions button")
    expect(page.locator("#queue-title")).to_have_text("Лабораторная 7 · ИС-22")

    student = browser.new_context(base_url=live_server)
    student_page = student.new_page()
    student_join(student_page, session["code"], "Анна")
    expect(student_page.locator("#session-title")).to_contain_text("Лабораторная 7 · ИС-22")
    student.close()


def test_teacher_creates_a_session_with_a_group_in_the_form(page: Page, open_session):
    session = open_session()
    teacher_login(page, session["username"], session["password"])
    page.fill("#title", "Практика")
    page.fill("#group", "ИС-23")
    page.click("#create-btn")

    expect(page.locator("#queue-title")).to_have_text("Практика · ИС-23")


def test_teacher_sees_a_russian_message_when_the_action_is_not_allowed(
    page: Page, api, open_session
):
    session = open_session()
    api.post(f"/sessions/{session['code']}/join", json={"name": "Один", "token": "o" * 16})
    queue = api.get(f"/sessions/{session['code']}/queue", headers=session["headers"]).json()
    waiting_id = queue["entries"][0]["id"]
    teacher_login(page, session["username"], session["password"])
    page.click("#sessions button")
    expect(row(page, "Один")).to_be_visible(timeout=SLOW)

    page.evaluate("id => entryAction(id, 'done')", waiting_id)

    expect(page.locator("#action-err")).to_have_text(
        "Отметить «сдал» можно только вызванного студента"
    )
    page.wait_for_timeout(1500)
    expect(page.locator("#action-err")).not_to_be_empty()


def test_student_sees_a_russian_message_when_the_join_request_cannot_reach_the_server(
    page: Page, open_session
):
    session = open_session()
    page.route("**/join", lambda route: route.abort())
    student_join(page, session["code"], "Анна")

    error = page.locator("#join-err")
    expect(error).to_have_text("Нет связи с сервером. Проверьте подключение и повторите")
    expect(page.locator("#queue-view")).to_be_hidden()


def test_a_stale_queue_response_does_not_overwrite_a_newer_screen(
    page: Page, browser, live_server, open_session
):
    session = open_session("Гонка ответов")
    contexts = []
    for name in ("Анна", "Борис"):
        context = browser.new_context(base_url=live_server)
        contexts.append(context)
        student_join(context.new_page(), session["code"], name)

    teacher_login(page, session["username"], session["password"])
    page.click("#sessions button")
    expect(row(page, "Борис")).to_be_visible(timeout=SLOW)

    held = []

    def hold_first(route):
        if held:
            route.continue_()
            return
        held.append((route, route.fetch()))

    page.route("**/sessions/*/queue", hold_first)
    page.evaluate("void refresh()")
    page.wait_for_timeout(500)
    assert held

    page.evaluate(
        """() => {
            window.__seen = false;
            window.__regressed = false;
            new MutationObserver(() => {
                const text = document.querySelector("#entries").innerText;
                if (text.includes("пропущен")) window.__seen = true;
                else if (window.__seen && text.includes("Анна")) window.__regressed = true;
            }).observe(document.querySelector("#entries"), { childList: true, subtree: true });
        }"""
    )
    row(page, "Анна").get_by_text("Пропустить").click()
    expect(row(page, "Анна")).to_contain_text("пропущен")
    route, stale = held[0]
    route.fulfill(response=stale)
    page.wait_for_timeout(1500)
    expect(row(page, "Анна")).to_contain_text("пропущен")
    assert page.evaluate("window.__regressed") is False
    for context in contexts:
        context.close()
