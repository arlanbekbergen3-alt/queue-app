import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_mobile_layout import PHONE, assert_fits
from tests.e2e.test_pages import SLOW
from tests.e2e.test_protocol_pages import login_as, prepare

pytestmark = pytest.mark.e2e


def complete_all(api, teacher, assignment, marks):
    headers = teacher["headers"]
    code = teacher["code"]
    criteria = api.get(f"/assignments/{assignment['id']}/criteria", headers=headers).json()
    for index, mark in enumerate(marks):
        joined = api.post(
            f"/sessions/{code}/join", json={"name": f"Студент {index}", "token": f"t{index}" * 8}
        )
        assert joined.status_code == 200, joined.text
        api.post(f"/sessions/{code}/next", headers=headers)
        current = api.get(f"/sessions/{code}/submissions/current", headers=headers).json()
        done = api.post(
            f"/submissions/{current['id']}/complete",
            headers=headers,
            json={"scores": [{"criterion_id": c["id"], "points": mark} for c in criteria]},
        )
        assert done.status_code == 200, done.text


def open_analytics(page: Page, teacher: dict, button: str) -> None:
    login_as(page, teacher)
    page.goto(f"/protocol?code={teacher['code']}")
    expect(page.locator("#p-body")).to_be_visible(timeout=SLOW)
    page.click(button)


def test_fairness_block_flags_a_tiring_queue_and_always_carries_the_caveat(
    page: Page, api, open_session
):
    teacher, assignment = prepare(api, open_session, questions=3)
    complete_all(api, teacher, assignment, [5, 5, 5, 5, 3, 3, 1, 1])

    open_analytics(page, teacher, "#an-session")

    box = page.locator("#fair-box")
    expect(box).to_contain_text("Справедливость оценивания", timeout=SLOW)
    expect(page.locator("#fair-fatigue")).to_contain_text("Возможен эффект усталости")
    expect(box).to_contain_text("Первые попытки и пересдачи")
    expect(box).to_contain_text("Разброс оценок и крайние оценки")
    expect(page.locator("#fair-disclaimer")).to_contain_text(
        "не доказывают ошибку или предвзятость"
    )
    expect(page.locator("#peers-note")).to_contain_text("Выключено")
    assert "Студент 0" not in box.inner_text()


def test_fairness_block_says_the_data_is_scarce(page: Page, api, open_session):
    teacher, assignment = prepare(api, open_session, questions=3)
    complete_all(api, teacher, assignment, [5, 3])

    open_analytics(page, teacher, "#an-assignment")

    expect(page.locator("#fair-note")).to_contain_text("Данных мало", timeout=SLOW)
    expect(page.locator("#fair-fatigue")).to_have_count(0)


def test_consent_to_share_summaries_is_saved_from_the_settings(page: Page, api, open_session):
    teacher, assignment = prepare(api, open_session, questions=3)
    login_as(page, teacher)
    page.goto("/protocol")
    page.locator("#assignments").get_by_role("button", name="Открыть").first.click()
    assert page.locator("#s-fair").is_checked() is False

    page.check("#s-fair")
    page.click("#s-save")
    expect(page.locator("#s-err")).to_contain_text("Сохранено", timeout=SLOW)

    saved = api.get(f"/assignments/{assignment['id']}", headers=teacher["headers"]).json()
    assert saved["fairness_sharing"] is True


def test_fairness_block_fits_a_phone_screen(page: Page, api, open_session):
    teacher, assignment = prepare(api, open_session, questions=3)
    complete_all(api, teacher, assignment, [5, 5, 5, 5, 3, 3, 1, 1])

    page.set_viewport_size(PHONE)
    open_analytics(page, teacher, "#an-assignment")

    expect(page.locator("#fair-fatigue")).to_be_visible(timeout=SLOW)
    assert_fits(page)
