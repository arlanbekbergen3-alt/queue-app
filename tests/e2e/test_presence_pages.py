import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import SLOW, row
from tests.e2e.test_protocol_pages import login_as

pytestmark = pytest.mark.e2e


def open_teacher(page: Page, teacher: dict) -> None:
    login_as(page, teacher)
    page.goto("/teacher")
    page.get_by_role("button", name=teacher["code"]).click()


def shown_code(page: Page) -> str:
    expect(page.locator("#presence-digits")).to_have_text(
        re.compile(r"^\d{3} \d{3}$"), timeout=SLOW
    )
    return page.locator("#presence-digits").inner_text().replace(" ", "")


def student(browser, live_server, code: str):
    context = browser.new_context(base_url=live_server)
    page = context.new_page()
    page.goto(f"/?code={code}")
    return context, page


def test_students_must_type_the_code_from_the_screen_and_the_teacher_can_switch_it_off(
    page: Page, browser, live_server, open_session, shot
):
    teacher = open_session("Присутствие")
    open_teacher(page, teacher)
    expect(page.locator("#presence-live")).to_be_hidden(timeout=SLOW)
    expect(page.locator("#presence-toggle")).to_have_text("Включить проверку присутствия")
    page.click("#presence-toggle")
    code = shown_code(page)
    expect(page.locator("#presence-left")).to_contain_text("Код обновится через", timeout=SLOW)
    expect(page.locator("#presence-toggle")).to_have_text("Выключить проверку присутствия")
    page.wait_for_function("document.getElementById('presence-qr').naturalWidth > 0")
    shot(page, "teacher-presence-code")

    ctx, anna = student(browser, live_server, teacher["code"])
    expect(anna.locator("#presence-box")).to_be_visible(timeout=SLOW)
    anna.fill("#name", "Анна Иванова")
    anna.click("#join-btn")
    expect(anna.locator("#join-err")).to_have_text(
        "Введите код с экрана преподавателя", timeout=SLOW
    )
    wrong = "000000" if code != "000000" else "111111"
    anna.fill("#presence-code", wrong)
    anna.click("#join-btn")
    expect(anna.locator("#join-err")).to_have_text(
        "Неверный код, посмотрите на экран", timeout=SLOW
    )
    expect(row(page, "Анна Иванова")).to_have_count(0)
    anna.fill("#presence-code", code[:3] + " " + code[3:])
    anna.click("#join-btn")
    expect(anna.locator("#queue-view")).to_be_visible(timeout=SLOW)
    expect(row(page, "Анна Иванова")).to_be_visible(timeout=SLOW)

    page.click("#presence-toggle")
    expect(page.locator("#presence-live")).to_be_hidden(timeout=SLOW)
    ctx2, boris = student(browser, live_server, teacher["code"])
    boris.fill("#name", "Борис Петров")
    expect(boris.locator("#presence-box")).to_be_hidden()
    boris.click("#join-btn")
    expect(boris.locator("#queue-view")).to_be_visible(timeout=SLOW)
    expect(row(page, "Анна Иванова")).to_be_visible()
    ctx.close()
    ctx2.close()


def test_the_link_from_the_qr_fills_the_code_so_joining_is_one_tap(
    page: Page, browser, live_server, open_session
):
    teacher = open_session("QR присутствие", presence_check="rotating_code")
    open_teacher(page, teacher)
    code = shown_code(page)
    ctx = browser.new_context(base_url=live_server)
    scanned = ctx.new_page()
    scanned.add_init_script(
        "try { localStorage.setItem('queue-name', 'Вера Смирнова'); } catch (e) {}"
    )
    scanned.goto(f"/?code={teacher['code']}&p={code}")
    expect(scanned.locator("#presence-code")).to_have_value(code, timeout=SLOW)
    scanned.click("#quick-join")
    expect(scanned.locator("#queue-view")).to_be_visible(timeout=SLOW)
    expect(row(page, "Вера Смирнова")).to_be_visible(timeout=SLOW)
    ctx.close()


def test_the_screen_asks_the_server_for_a_new_code_when_the_old_one_expires(
    page: Page, open_session
):
    teacher = open_session("Смена кода", presence_check="rotating_code", presence_interval=30)
    seen = []
    page.on(
        "request",
        lambda request: seen.append(request.url) if request.url.endswith("/presence") else None,
    )
    page.clock.install()
    open_teacher(page, teacher)
    shown_code(page)
    before = len(seen)
    page.clock.fast_forward(35_000)
    for _ in range(60):
        if len(seen) > before:
            break
        page.wait_for_timeout(250)
    assert len(seen) > before
    expect(page.locator("#presence-digits")).to_have_text(re.compile(r"^\d{3} \d{3}$"))


def test_a_closed_registration_page_and_a_session_without_the_check_show_no_code_field(
    page: Page, browser, live_server, open_session
):
    teacher = open_session("Без проверки")
    ctx, plain = student(browser, live_server, teacher["code"])
    plain.fill("#name", "Глеб Орлов")
    expect(plain.locator("#presence-box")).to_be_hidden()
    expect(plain.locator("#join-view")).to_be_visible()
    ctx.close()
    with_check = open_session("С проверкой", presence_check="rotating_code")
    ctx2, other = student(browser, live_server, with_check["code"])
    expect(other.locator("#presence-box")).to_be_visible(timeout=SLOW)
    ctx2.close()
