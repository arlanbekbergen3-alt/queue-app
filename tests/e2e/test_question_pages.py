import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_mobile_layout import PHONE, assert_fits
from tests.e2e.test_pages import SLOW
from tests.e2e.test_protocol_pages import login_as

pytestmark = pytest.mark.e2e

SHORT = "В банке 2 вопроса, а на сдачу нужно 3: каждый студент получит 2"


def thin_assignment(api, open_session) -> dict:
    teacher = open_session("Мало вопросов")
    headers = teacher["headers"]
    assignment = api.post(
        "/assignments",
        headers=headers,
        json={"title": "Тонкая работа", "questions_per_submission": 3},
    ).json()
    for i in range(2):
        api.post(
            f"/assignments/{assignment['id']}/questions",
            headers=headers,
            json={"text": f"Вопрос {i}", "difficulty": 2, "topic": "t"},
        )
    return teacher


def test_the_question_count_has_a_clear_label_and_a_hint(page: Page, api, open_session):
    teacher = thin_assignment(api, open_session)
    login_as(page, teacher)
    page.goto("/protocol")

    label = page.locator('label[for="a-count"]')
    expect(label).to_contain_text("Сколько вопросов выпадает каждому студенту", timeout=SLOW)
    expect(page.locator("#a-count")).to_be_visible()
    expect(page.get_by_text("Если в банке вопросов меньше, студент получит все.")).to_be_visible()
    page.locator("#assignments").get_by_role("button", name="Открыть").first.click()
    expect(page.locator('label[for="s-count"]')).to_contain_text("Сколько вопросов выпадает")


def test_the_work_settings_warn_when_the_bank_is_smaller_than_needed(page: Page, api, open_session):
    teacher = thin_assignment(api, open_session)
    login_as(page, teacher)
    page.goto("/protocol")
    page.locator("#assignments").get_by_role("button", name="Открыть").first.click()

    expect(page.locator("#s-short")).to_contain_text(SHORT, timeout=SLOW)
    page.fill("#s-count", "2")
    page.click("#s-save")
    expect(page.locator("#s-err")).to_contain_text("Сохранено", timeout=SLOW)
    page.reload()
    page.locator("#assignments").get_by_role("button", name="Открыть").first.click()
    expect(page.locator("#s-short")).to_have_text("", timeout=SLOW)


def test_the_warning_shows_when_attaching_the_work_to_a_session(page: Page, api, open_session):
    teacher = thin_assignment(api, open_session)
    login_as(page, teacher)
    page.goto(f"/protocol?code={teacher['code']}")
    expect(page.locator("#p-body")).to_be_visible(timeout=SLOW)

    page.select_option("#p-assignment", label="Тонкая работа")

    expect(page.locator("#p-short")).to_contain_text(SHORT)
    page.select_option("#p-assignment", value="")
    expect(page.locator("#p-short")).to_have_text("")


def test_the_warning_shows_when_creating_a_session(page: Page, api, open_session):
    teacher = thin_assignment(api, open_session)
    login_as(page, teacher)
    page.goto("/teacher")
    expect(page.locator("#mode")).to_be_visible(timeout=SLOW)

    page.select_option("#w-assignment", label="Тонкая работа")

    expect(page.locator("#w-short")).to_contain_text(SHORT, timeout=SLOW)


def test_the_new_labels_fit_a_phone(page: Page, api, open_session):
    teacher = thin_assignment(api, open_session)
    page.set_viewport_size(PHONE)
    login_as(page, teacher)
    page.goto("/protocol")
    page.locator("#assignments").get_by_role("button", name="Открыть").first.click()
    expect(page.locator("#s-short")).to_contain_text(SHORT, timeout=SLOW)
    assert_fits(page)
