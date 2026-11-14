import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_mobile_layout import PHONE, assert_fits
from tests.e2e.test_pages import SLOW
from tests.e2e.test_protocol_pages import login_as, prepare
from tests.e2e.test_workspace_pages import open_teacher_page

pytestmark = pytest.mark.e2e

HISTORY = [3, 4, 3, 4, 3, 4]


def seed(api, teacher, assignment, *, history=HISTORY):
    headers = teacher["headers"]
    code = teacher["code"]
    criteria = api.get(f"/assignments/{assignment['id']}/criteria", headers=headers).json()
    api.patch(
        f"/assignments/{assignment['id']}",
        headers=headers,
        json={"require_extreme_reason": True, "deviation_warning": True},
    )
    for i, points in enumerate(history):
        api.post(f"/sessions/{code}/join", json={"name": f"Прежний {i}", "token": f"old{i}" * 5})
        api.post(f"/sessions/{code}/next", headers=headers)
        current = api.get(f"/sessions/{code}/submissions/current", headers=headers).json()
        done = api.post(
            f"/submissions/{current['id']}/complete",
            headers=headers,
            json={"scores": [{"criterion_id": c["id"], "points": points} for c in criteria]},
        )
        assert done.status_code == 200, done.text
    joined = api.post(f"/sessions/{code}/join", json={"name": "Новая Студентка", "token": "n" * 20})
    assert joined.status_code == 200, joined.text
    return criteria


def test_teacher_screen_asks_for_a_reason_and_shows_the_soft_warning(page: Page, api, open_session):
    teacher, assignment = prepare(api, open_session, questions=3)
    criteria = seed(api, teacher, assignment)
    headers = teacher["headers"]

    open_teacher_page(page, teacher)
    page.get_by_role("button", name=teacher["code"]).click()
    page.keyboard.press("n")
    expect(page.locator("#work-card")).to_be_visible(timeout=SLOW)
    first = page.locator(f"#crit-{criteria[0]['id']}")
    second = page.locator(f"#crit-{criteria[1]['id']}")
    expect(first.locator(".why")).to_be_hidden()

    first.locator("button[data-points='0']").click()
    expect(first.locator(".why input")).to_be_visible()
    expect(first.locator(".dev")).to_contain_text(
        "обычно около 3,5, сейчас 0. Проверьте.", timeout=SLOW
    )
    second.locator("button[data-points='4']").click()
    expect(second.locator(".dev")).to_be_hidden()
    expect(second.locator(".why")).to_be_hidden()

    page.click("#work-finish")
    expect(page.locator("#work-err")).to_contain_text("пояснение", timeout=SLOW)
    first.locator(".why input").fill("Нет ответа на вопрос")
    page.click("#work-finish")
    expect(page.locator("#work-card")).to_be_hidden(timeout=SLOW)

    done = api.get(f"/sessions/{teacher['code']}/submissions", headers=headers).json()[-1]
    assert done["status"] == "completed"
    scores = {s["criterion_id"]: s for s in done["scores"]}
    assert scores[criteria[0]["id"]]["points"] == 0
    assert scores[criteria[0]["id"]]["comment"] == "Нет ответа на вопрос"
    log = api.get(f"/sessions/{teacher['code']}/events-log", headers=headers).json()
    assert [e["kind"] for e in log] == ["deviation_shown"]


def test_teacher_screen_stays_quiet_when_the_features_are_off(page: Page, api, open_session):
    teacher, assignment = prepare(api, open_session, questions=3)
    headers = teacher["headers"]
    code = teacher["code"]
    api.post(f"/sessions/{code}/join", json={"name": "Тихая Студентка", "token": "q" * 20})

    open_teacher_page(page, teacher)
    page.get_by_role("button", name=code).click()
    page.keyboard.press("n")
    expect(page.locator("#work-card")).to_be_visible(timeout=SLOW)
    criteria = api.get(f"/assignments/{assignment['id']}/criteria", headers=headers).json()
    first = page.locator(f"#crit-{criteria[0]['id']}")
    first.locator("button[data-points='0']").click()
    page.wait_for_timeout(800)

    expect(first.locator(".why")).to_be_hidden()
    expect(first.locator(".dev")).to_be_hidden()
    assert api.get(f"/sessions/{code}/events-log", headers=headers).json() == []


def test_protocol_screen_settings_reason_and_warning(page: Page, api, open_session):
    teacher, assignment = prepare(api, open_session, questions=3)
    criteria = seed(api, teacher, assignment)
    api.patch(
        f"/assignments/{assignment['id']}",
        headers=teacher["headers"],
        json={"require_extreme_reason": False, "deviation_warning": False},
    )
    api.post(f"/sessions/{teacher['code']}/next", headers=teacher["headers"])

    login_as(page, teacher)
    page.goto("/protocol")
    page.locator("#assignments").get_by_role("button", name="Открыть").first.click()
    page.check("#s-reason")
    page.check("#s-deviation")
    page.fill("#s-sigma", "1.5")
    page.click("#s-save")
    expect(page.locator("#s-err")).to_contain_text("Сохранено", timeout=SLOW)
    saved = api.get(f"/assignments/{assignment['id']}", headers=teacher["headers"]).json()
    assert saved["require_extreme_reason"] is True and saved["deviation_warning"] is True
    assert saved["deviation_sigma"] == 1.5

    page.goto(f"/protocol?code={teacher['code']}")
    current = page.locator("#current")
    expect(current).to_contain_text("Талон №", timeout=SLOW)
    box = current.locator(".score").first
    expect(box.locator(".why")).to_be_hidden()
    box.locator(".pts button", has_text="0").first.click()
    expect(box.locator(".why")).to_be_visible()
    expect(box.locator(".dev")).to_contain_text("обычно около 3,5, сейчас 0", timeout=SLOW)
    for other in range(1, len(criteria)):
        current.locator(".score").nth(other).locator(".pts button", has_text="4").first.click()

    current.get_by_role("button", name="Завершить сдачу").click()
    expect(current.locator(".err")).to_contain_text("пояснение", timeout=SLOW)
    box.locator(".why").fill("Студент не ответил")
    current.get_by_role("button", name="Завершить сдачу").click()
    expect(current).to_contain_text("Нет вызванного студента", timeout=SLOW)


def test_reason_and_warning_fit_a_phone_screen(page: Page, api, open_session):
    teacher, assignment = prepare(api, open_session, questions=3)
    criteria = seed(api, teacher, assignment)
    api.post(f"/sessions/{teacher['code']}/next", headers=teacher["headers"])

    page.set_viewport_size(PHONE)
    open_teacher_page(page, teacher)
    page.get_by_role("button", name=teacher["code"]).click()
    first = page.locator(f"#crit-{criteria[0]['id']}")
    expect(first).to_be_visible(timeout=SLOW)
    first.locator("button[data-points='5']").click()
    expect(first.locator(".dev")).to_contain_text("обычно около", timeout=SLOW)
    expect(first.locator(".why input")).to_be_visible()
    assert_fits(page)

    page.goto(f"/protocol?code={teacher['code']}")
    box = page.locator("#current .score").first
    expect(box).to_be_visible(timeout=SLOW)
    box.locator(".pts button", has_text="0").first.click()
    expect(box.locator(".dev")).to_be_visible(timeout=SLOW)
    expect(box.locator(".why")).to_be_visible()
    assert_fits(page)
    page.goto("/protocol")
    page.locator("#assignments").get_by_role("button", name="Открыть").first.click()
    expect(page.locator("#s-reason")).to_be_visible(timeout=SLOW)
    assert_fits(page)
