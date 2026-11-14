import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_protocol_pages import login_as, prepare

pytestmark = pytest.mark.e2e

SLOW = 20_000


def test_two_teachers_share_a_bank_through_a_subject_group(
    page: Page, browser, live_server, api, open_session
):
    owner, assignment = prepare(api, open_session, questions=3)
    reader = open_session("Читатель")
    login_as(page, owner)
    page.goto("/spaces")

    page.fill("#s-name", "Базы данных, 2 курс")
    page.click("#s-create")
    expect(page.locator("#c-name")).to_have_text("Базы данных, 2 курс", timeout=SLOW)
    expect(page.locator("#invite-box")).to_be_visible()
    invite = page.locator("#c-invite").inner_text().strip()
    assert len(invite) == 10

    page.select_option("#p-assignment", assignment["id"])
    page.click("#p-go")
    expect(page.locator("#p-err")).to_have_text("Опубликовано", timeout=SLOW)
    expect(page.locator("#pubs > div")).to_have_count(1)
    expect(page.locator("#pubs")).to_contain_text("3 вопроса")

    other = browser.new_context(base_url=live_server)
    page2 = other.new_page()
    login_as(page2, reader)
    page2.goto("/spaces")
    page2.fill("#j-code", "неверный-код")
    page2.click("#j-go")
    expect(page2.locator("#list-err")).to_have_text("Неверный код приглашения", timeout=SLOW)

    page2.fill("#j-code", invite)
    page2.click("#j-go")
    expect(page2.locator("#c-name")).to_have_text("Базы данных, 2 курс", timeout=SLOW)
    expect(page2.locator("#invite-box")).to_be_hidden()
    expect(page2.locator("#c-delete")).to_be_hidden()
    expect(page2.locator("#members")).to_contain_text(owner["username"])
    expect(page2.locator("#pubs")).to_contain_text(f"автор: {owner['username']}")
    expect(page2.locator("#pubs").get_by_role("button", name="Снять")).to_have_count(0)

    page2.locator("#pubs").get_by_role("button", name="Показать").click()
    expect(page2.locator("#preview")).to_contain_text("Вопрос номер 0", timeout=SLOW)
    expect(page2.locator("#preview")).to_contain_text("Код (максимум 5 баллов)")

    page2.locator("#pubs").get_by_role("button", name="Скопировать к себе").click()
    expect(page2.locator("#c-err")).to_contain_text("Создана работа", timeout=SLOW)

    page2.goto("/protocol")
    row = page2.locator("#assignments > div")
    expect(row).to_have_count(1, timeout=SLOW)
    expect(row).to_contain_text("из группы: Базы данных, 2 курс")
    row.get_by_role("button", name="Открыть").click()
    expect(page2.locator("#a-origin")).to_contain_text(owner["username"], timeout=SLOW)
    expect(page2.locator("#questions > div")).to_have_count(3)

    page2.goto("/spaces")
    page2.locator("#spaces > div").get_by_role("button", name="Открыть").click()
    page2.once("dialog", lambda dialog: dialog.accept())
    page2.click("#c-leave")
    expect(page2.locator("#card")).to_be_hidden(timeout=SLOW)
    expect(page2.locator("#spaces")).to_contain_text("пока не состоите")

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#pubs").get_by_role("button", name="Снять").click()
    expect(page.locator("#pubs")).to_contain_text("Материалов пока нет", timeout=SLOW)
    other.close()
