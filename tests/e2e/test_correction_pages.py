import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import SLOW
from tests.e2e.test_protocol_pages import login_as, prepare

pytestmark = pytest.mark.e2e


def finished_submission(page: Page, api, open_session) -> dict:
    teacher, _ = prepare(api, open_session)
    code = teacher["code"]
    api.post(f"/sessions/{code}/join", json={"name": "Борис", "token": "b" * 20})
    api.post(f"/sessions/{code}/next", headers=teacher["headers"])
    login_as(page, teacher)
    page.goto(f"/protocol?code={code}")
    current = page.locator("#current")
    expect(current).to_contain_text("Талон №1", timeout=SLOW)
    inputs = current.locator("input[type=number]")
    inputs.nth(0).fill("2")
    inputs.nth(1).fill("2")
    current.get_by_role("button", name="Завершить сдачу").click()
    expect(page.locator("#done")).to_contain_text("2/", timeout=SLOW)
    return teacher


def test_a_double_click_on_edit_opens_one_form_and_a_second_click_closes_it(
    page: Page, api, open_session
):
    finished_submission(page, api, open_session)
    edit = page.locator("#done").get_by_role("button", name="Править")

    edit.dblclick()

    expect(page.get_by_role("button", name="Сохранить правку")).to_have_count(1, timeout=SLOW)
    page.wait_for_timeout(400)
    assert page.get_by_role("button", name="Сохранить правку").count() == 1
    page.locator("#done").get_by_role("button", name="Закрыть правку").click()
    expect(page.get_by_role("button", name="Сохранить правку")).to_have_count(0)
    page.locator("#done").get_by_role("button", name="Править").click()
    expect(page.get_by_role("button", name="Сохранить правку")).to_have_count(1)


def test_a_double_click_on_save_writes_one_journal_entry(page: Page, api, open_session):
    teacher = finished_submission(page, api, open_session)
    page.locator("#done").get_by_role("button", name="Править").click()
    page.locator("#done input[type=number]").first.fill("5")
    page.get_by_placeholder("Причина правки (обязательно)").fill("Пересмотр защиты")

    page.get_by_role("button", name="Сохранить правку").dblclick()

    expect(page.locator("#done")).to_contain_text("7/", timeout=SLOW)
    submissions = api.get(
        f"/sessions/{teacher['code']}/submissions", headers=teacher["headers"]
    ).json()
    completed = next(s for s in submissions if s["status"] == "completed")
    audit = api.get(f"/submissions/{completed['id']}/audit", headers=teacher["headers"]).json()
    assert len([a for a in audit if a["action"] == "correct"]) == 1
