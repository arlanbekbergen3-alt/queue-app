import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import SLOW
from tests.e2e.test_protocol_pages import login_as

pytestmark = pytest.mark.e2e

LIST = "1. Иванов Иван\n2. Петрова Анна\nСидоров Пётр"


def upload(api, teacher, group="ИС-22", text=LIST):
    response = api.put("/roster", headers=teacher["headers"], json={"group": group, "text": text})
    assert response.status_code == 200, response.text


def student(browser, live_server, code: str):
    context = browser.new_context(base_url=live_server)
    page = context.new_page()
    page.goto(f"/?code={code}")
    return context, page


def test_the_teacher_uploads_a_list_from_the_students_page_and_sees_what_was_skipped(
    page: Page, teacher_api
):
    teacher = teacher_api()
    login_as(page, teacher)
    page.goto("/students")
    page.fill("#r-group", "ИС-22")
    page.fill("#r-text", LIST + "\nиванов иван")
    page.click("#r-save")
    expect(page.locator("#r-err")).to_contain_text("Сохранено: 3 человека", timeout=SLOW)
    expect(page.locator("#r-err")).to_contain_text("повтор")
    expect(page.locator("#r-groups")).to_contain_text("ИС-22 · 3 человека")
    expect(page.locator("#r-text")).to_have_value("Иванов Иван\nПетрова Анна\nСидоров Пётр")

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#r-groups").get_by_role("button", name="Удалить").click()
    expect(page.locator("#r-groups")).to_contain_text("Списков пока нет.", timeout=SLOW)


def test_the_session_form_offers_the_list_only_for_a_group_that_has_one(
    page: Page, teacher_api, api
):
    teacher = teacher_api()
    upload(api, teacher)
    login_as(page, teacher)
    page.goto("/teacher")
    page.fill("#group", "ис - 22")
    expect(page.locator("#roster-only-row")).to_be_visible(timeout=SLOW)
    expect(page.locator("#roster-hint")).to_contain_text("3 человека")
    page.fill("#group", "ПО-11")
    expect(page.locator("#roster-only-row")).to_be_hidden()

    page.fill("#group", "ИС-22")
    page.check("#roster-only")
    page.click("#create-btn")
    expect(page.locator("#queue-title")).to_contain_text("ИС-22", timeout=SLOW)
    sessions = api.get("/sessions", headers=teacher["headers"]).json()
    assert sessions[0]["roster_only"] is True


def test_a_student_picks_their_name_from_the_list_and_cannot_type_another(
    browser, live_server, api, open_session, teacher_api
):
    teacher = open_session("Зачёт", group="ИС-22")
    upload(api, teacher)
    api.put(
        f"/sessions/{teacher['code']}/settings",
        headers=teacher["headers"],
        json={"roster_only": True},
    )
    ctx, anna = student(browser, live_server, teacher["code"])
    expect(anna.locator("#roster-box")).to_be_visible(timeout=SLOW)
    expect(anna.locator("#roster-list button")).to_have_count(3)
    expect(anna.locator("#name")).to_have_attribute("readonly", "")

    anna.get_by_role("button", name="Петрова Анна").click()
    expect(anna.locator("#name")).to_have_value("Петрова Анна")
    anna.click("#join-btn")
    expect(anna.locator("#status-badge")).to_have_text("В очереди", timeout=SLOW)

    other, boris = student(browser, live_server, teacher["code"])
    expect(boris.locator("#roster-list button")).to_have_count(2, timeout=SLOW)
    expect(boris.locator("#roster-list")).not_to_contain_text("Петрова Анна")
    ctx.close()
    other.close()


def test_without_the_restriction_the_list_helps_but_any_name_still_works(
    browser, live_server, api, open_session
):
    teacher = open_session("Зачёт", group="ИС-22")
    upload(api, teacher)
    ctx, vera = student(browser, live_server, teacher["code"])
    expect(vera.locator("#roster-list button")).to_have_count(3, timeout=SLOW)
    expect(vera.locator("#name")).not_to_have_attribute("readonly", "")

    vera.fill("#name", "Гость Группы")
    vera.click("#join-btn")
    expect(vera.locator("#status-badge")).to_have_text("В очереди", timeout=SLOW)
    ctx.close()


def test_a_session_without_a_list_shows_no_list(browser, live_server, open_session):
    teacher = open_session("Зачёт", group="Без списка")
    ctx, page = student(browser, live_server, teacher["code"])
    page.fill("#name", "Кто Угодно")
    expect(page.locator("#roster-box")).to_be_hidden()
    ctx.close()
