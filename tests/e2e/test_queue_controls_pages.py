import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import SLOW, order, row, student_join, teacher_login

pytestmark = pytest.mark.e2e


def test_student_defers_and_returns_while_the_teacher_sees_it_and_closes_registration(
    page: Page, browser, live_server, open_session
):
    session = open_session()
    code = session["code"]
    other = browser.new_context(base_url=live_server)
    other_page = other.new_page()
    student_join(other_page, code, "Борис Петров")
    student_join(page, code, "Анна Иванова")
    expect(page.locator("#position")).to_have_text("2", timeout=SLOW)

    teacher_ctx = browser.new_context(base_url=live_server)
    teacher = teacher_ctx.new_page()
    teacher_login(teacher, session["username"], session["password"])
    teacher.get_by_role("button", name=code).click()
    expect(row(teacher, "Анна Иванова")).to_be_visible(timeout=SLOW)

    other_page.click("#notready-btn")
    expect(other_page.locator("#position")).to_have_text("2", timeout=SLOW)
    expect(other_page.locator("#ready-btn")).to_be_visible()
    expect(page.locator("#position")).to_have_text("1", timeout=SLOW)
    expect(row(teacher, "Борис Петров")).to_contain_text("не готов", timeout=SLOW)
    assert order(teacher) == ["Анна Иванова", "Борис Петров"]

    other_page.click("#ready-btn")
    expect(other_page.locator("#position")).to_have_text("1", timeout=SLOW)
    expect(row(teacher, "Борис Петров")).not_to_contain_text("не готов", timeout=SLOW)
    expect(row(teacher, "Борис Петров")).to_contain_text("×1")

    teacher.click("#registration-btn")
    expect(teacher.locator("#registration-btn")).to_have_text("Открыть запись", timeout=SLOW)
    late = browser.new_context(base_url=live_server)
    late_page = late.new_page()
    student_join(late_page, code, "Опоздавший")
    expect(late_page.locator("#join-err")).to_have_text("Запись закрыта", timeout=SLOW)
    expect(page.locator("#queue-view")).to_be_visible()

    teacher.click("#registration-btn")
    expect(teacher.locator("#registration-btn")).to_have_text("Закрыть запись", timeout=SLOW)
    student_join(late_page, code, "Опоздавший")
    expect(late_page.locator("#queue-view")).to_be_visible(timeout=SLOW)
    for ctx in (other, teacher_ctx, late):
        ctx.close()


def test_defer_button_disappears_when_the_limit_is_used_up(page: Page, open_session):
    session = open_session()
    student_join(page, session["code"], "Анна Иванова")
    expect(page.locator("#queue-view")).to_be_visible(timeout=SLOW)
    expect(page.locator("#notready-btn")).to_be_visible()

    for _ in range(2):
        page.click("#notready-btn")
        expect(page.locator("#ready-btn")).to_be_visible(timeout=SLOW)
        page.click("#ready-btn")
        expect(page.locator("#ready-btn")).to_be_hidden(timeout=SLOW)

    expect(page.locator("#notready-btn")).to_be_hidden(timeout=SLOW)
    expect(page.locator("#notready-note")).to_contain_text("закончились")
