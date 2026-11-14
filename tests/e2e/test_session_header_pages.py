import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_mobile_layout import PHONE, assert_fits
from tests.e2e.test_pages import SLOW, student_join
from tests.e2e.test_protocol_pages import login_as

pytestmark = pytest.mark.e2e


def teacher_page(page: Page, teacher: dict) -> None:
    login_as(page, teacher)
    page.goto("/teacher")
    page.get_by_role("button", name=teacher["code"]).click()
    expect(page.locator("#queue-code")).to_have_text(teacher["code"], timeout=SLOW)


def test_the_code_and_the_address_appear_once_and_the_qr_is_collapsed(page: Page, open_session):
    teacher = open_session("Один раз")
    teacher_page(page, teacher)

    assert page.locator("#queue-code").count() == 1
    assert page.get_by_text(teacher["code"], exact=True).count() == 1
    assert page.locator("#start-panel").evaluate("node => node.open") is False
    expect(page.locator("#start-qr")).to_be_hidden()
    page.click("#start-panel summary")
    expect(page.locator("#start-qr")).to_be_visible()


def test_the_copy_button_confirms_the_copy_and_the_note_fades(page: Page, open_session):
    teacher = open_session("Копирование")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    teacher_page(page, teacher)

    page.click("#copy-link")

    expect(page.locator("#copy-note")).to_have_text("Скопировано")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert copied.endswith(f"/?code={teacher['code']}")
    expect(page.locator("#copy-note")).to_have_text("", timeout=5000)


def test_the_projector_shows_the_code_and_closes_on_escape(page: Page, open_session):
    teacher = open_session("Проектор")
    teacher_page(page, teacher)

    page.get_by_role("button", name="Показать для проектора").click()

    expect(page.locator("#projector")).to_be_visible()
    expect(page.locator("#projector-code")).to_have_text(teacher["code"])
    page.keyboard.press("Escape")
    expect(page.locator("#projector")).to_be_hidden()


def test_the_help_button_has_a_label_and_the_presence_block_is_compact(page: Page, open_session):
    teacher = open_session("Подписи")
    teacher_page(page, teacher)

    expect(page.locator("#help-btn")).to_contain_text("Клавиши")
    assert page.locator("#help-btn").get_attribute("aria-label")
    expect(page.locator("#presence-toggle")).to_be_visible()
    expect(page.locator("#presence-interval-live")).to_be_hidden()
    page.click("#presence-panel summary")
    expect(page.locator("#presence-interval-live")).to_be_visible()


def test_the_group_is_not_repeated_when_the_title_already_names_it(page: Page, api, open_session):
    teacher = open_session("По2502, 14.11 10:00", group="По2502")
    teacher_page(page, teacher)

    expect(page.locator("#queue-title")).to_have_text("По2502, 14.11 10:00")
    student_join(page, teacher["code"], "Анна Иванова")
    expect(page.locator("#session-title")).to_have_text(
        "По2502, 14.11 10:00 (" + teacher["code"] + ")"
    )


def test_a_finished_student_sees_a_short_note_without_the_queue_details(
    page: Page, api, open_session
):
    teacher = open_session("Завершение", session_mode="queue_only")
    code = teacher["code"]
    student_join(page, code, "Анна Иванова")
    expect(page.locator("#queue-view")).to_be_visible(timeout=SLOW)
    expect(page.locator("#row-now")).to_be_visible()
    called = api.post(f"/sessions/{code}/next", headers=teacher["headers"]).json()
    api.post(f"/sessions/{code}/entries/{called['id']}/done", headers=teacher["headers"])

    expect(page.locator("#message")).to_contain_text(
        "Сдача завершена, результат ниже", timeout=SLOW
    )
    expect(page.locator("#row-now")).to_be_hidden()
    expect(page.locator("#row-eta")).to_be_hidden()
    expect(page.locator("#session-title")).to_have_text("Завершение")
    assert "(" not in page.locator("#session-title").inner_text()


def test_the_teacher_switches_names_for_students_and_students_see_only_a_ticket(
    page: Page, browser, live_server, api, open_session
):
    teacher = open_session("Имена")
    code = teacher["code"]
    other = browser.new_context(base_url=live_server)
    watcher = other.new_page()
    student_join(page, code, "Секретный Студент")
    expect(page.locator("#queue-view")).to_be_visible(timeout=SLOW)
    student_join(watcher, code, "Наблюдатель")
    expect(watcher.locator("#queue-view")).to_be_visible(timeout=SLOW)
    api.post(f"/sessions/{code}/next", headers=teacher["headers"])

    expect(watcher.locator("#now-serving")).to_have_text("№ 1", timeout=SLOW)
    assert "Секретный" not in watcher.content()

    admin = other.new_page()
    login_as(admin, teacher)
    admin.goto("/teacher")
    admin.get_by_role("button", name=code).click()
    admin.check("#live-names")
    expect(watcher.locator("#now-serving")).to_contain_text("Секретный Студент", timeout=SLOW)
    other.close()


def test_the_new_header_fits_a_phone(page: Page, open_session):
    teacher = open_session("По2502, 14.11 10:00 очень длинное название занятия", group="По2502")
    page.set_viewport_size(PHONE)
    teacher_page(page, teacher)
    page.click("#start-panel summary")
    page.click("#presence-panel summary")
    expect(page.locator("#start-qr")).to_be_visible()
    assert_fits(page)
