import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_protocol_pages import login_as, prepare

pytestmark = pytest.mark.e2e

SLOW = 20_000


def test_teacher_copies_a_work_and_creates_another_from_a_template(page: Page, api, open_session):
    teacher, assignment = prepare(api, open_session, questions=3)
    login_as(page, teacher)
    page.goto("/protocol")
    row = page.locator("#assignments > div", has_text=assignment["title"])
    expect(row).to_be_visible(timeout=SLOW)
    row.get_by_role("button", name="Открыть").click()
    expect(page.locator("#a-name")).to_have_text(assignment["title"], timeout=SLOW)
    expect(page.locator("#tpl-row")).to_be_hidden()

    page.fill("#copy-title", "Весенняя копия")
    page.click("#a-copy")
    expect(page.locator("#a-name")).to_have_text("Весенняя копия", timeout=SLOW)
    expect(page.locator("#questions > div")).to_have_count(3)
    expect(page.locator("#criteria > div")).to_have_count(2)
    expect(page.locator("#assignments > div", has_text="Весенняя копия")).to_contain_text("0 сдач")

    page.fill("#copy-title", "Весенняя копия")
    page.click("#a-copy")
    expect(page.locator("#s-err")).to_have_text("Работа с таким названием уже есть", timeout=SLOW)

    page.locator("#assignments > div", has_text=assignment["title"]).first.get_by_role(
        "button", name="Открыть"
    ).click()
    expect(page.locator("#a-name")).to_have_text(assignment["title"], timeout=SLOW)
    page.check("#s-template")
    page.click("#s-save")
    expect(page.locator("#s-err")).to_have_text("Сохранено", timeout=SLOW)
    expect(page.locator("#tpl-row")).to_be_visible(timeout=SLOW)
    expect(page.locator("#assignments > div", has_text=assignment["title"]).first).to_contain_text(
        "шаблон"
    )

    page.fill("#a-title", "Из шаблона")
    page.click("#tpl-create")
    expect(page.locator("#a-name")).to_have_text("Из шаблона", timeout=SLOW)
    expect(page.locator("#questions > div")).to_have_count(3)
    expect(page.locator("#s-template")).not_to_be_checked()
