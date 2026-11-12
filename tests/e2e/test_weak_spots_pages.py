import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_protocol_pages import login_as, prepare

pytestmark = pytest.mark.e2e

SLOW = 20_000


def finish(api, teacher, criteria, index, points):
    code = teacher["code"]
    token = f"weak-spots-{index:03d}-" + "x" * 8
    api.post(f"/sessions/{code}/join", json={"name": f"Студент {index}", "token": token})
    api.post(f"/sessions/{code}/next", headers=teacher["headers"])
    current = api.get(f"/sessions/{code}/submissions/current", headers=teacher["headers"]).json()
    scores = [{"criterion_id": c["id"], "points": p} for c, p in zip(criteria, points, strict=True)]
    done = api.post(
        f"/submissions/{current['id']}/complete",
        headers=teacher["headers"],
        json={"scores": scores},
    )
    assert done.status_code == 200, done.text


def test_weak_spots_table_and_bars_appear_only_with_enough_submissions(
    page: Page, api, open_session
):
    teacher, assignment = prepare(api, open_session, questions=2)
    criteria = api.get(
        f"/assignments/{assignment['id']}/criteria", headers=teacher["headers"]
    ).json()
    login_as(page, teacher)
    page.goto(f"/protocol?code={teacher['code']}")
    expect(page.locator("#need-login")).to_be_hidden(timeout=SLOW)

    for index, points in enumerate([(5, 1), (4, 2), (5, 0), (4, 1)]):
        finish(api, teacher, criteria, index, points)
    page.click("#an-session")
    expect(page.locator("#an-weak")).to_contain_text("Мало данных", timeout=SLOW)
    expect(page.locator("#an-weak table")).to_have_count(0)

    finish(api, teacher, criteria, 4, (3, 2))
    page.click("#an-session")
    table = page.locator("#an-weak table").first
    expect(table).to_be_visible(timeout=SLOW)
    expect(table.locator("tbody tr")).to_have_count(2)
    expect(table.locator("tbody tr").first).to_contain_text("Защита")
    expect(table.locator("tbody tr").first).to_contain_text("24%")
    expect(table.locator("tbody tr").first).to_contain_text("100%")
    expect(table.locator("tbody tr").nth(1)).to_contain_text("Код")
    expect(table.locator("tbody tr").nth(1)).to_contain_text("84%")
    expect(page.locator("#an-weak .bar").first).to_be_visible()
    expect(page.locator("#an-weak .bins")).to_contain_text("%")
    expect(page.locator("#an-weak")).to_contain_text("Самые проблемные: Защита, Код")
    expect(page.locator("#an-weak")).not_to_contain_text("Студент")

    page.click("#an-assignment")
    expect(page.locator("#an-weak table").first).to_be_visible(timeout=SLOW)
