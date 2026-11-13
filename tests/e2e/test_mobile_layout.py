import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import student_join
from tests.e2e.test_protocol_pages import login_as, prepare

pytestmark = pytest.mark.e2e

SLOW = 20_000
PHONE = {"width": 360, "height": 740}


def overflow(page: Page) -> int:
    return page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


def test_new_pages_do_not_scroll_sideways_on_a_phone(page: Page, api, open_session):
    teacher, assignment = prepare(api, open_session, questions=3)
    code = teacher["code"]
    for index in range(5):
        token = f"mobile-layout-{index:03d}-" + "x" * 6
        api.post(f"/sessions/{code}/join", json={"name": f"Студент {index}", "token": token})
        api.post(f"/sessions/{code}/next", headers=teacher["headers"])
        current = api.get(
            f"/sessions/{code}/submissions/current", headers=teacher["headers"]
        ).json()
        criteria = api.get(
            f"/assignments/{assignment['id']}/criteria", headers=teacher["headers"]
        ).json()
        api.post(
            f"/submissions/{current['id']}/complete",
            headers=teacher["headers"],
            json={"scores": [{"criterion_id": c["id"], "points": index % 5} for c in criteria]},
        )
    space = api.post(
        "/spaces",
        headers=teacher["headers"],
        json={"name": "Очень длинное название предметной группы"},
    ).json()
    api.post(
        f"/spaces/{space['id']}/publications",
        headers=teacher["headers"],
        json={"assignment_id": assignment["id"]},
    )
    page.set_viewport_size(PHONE)
    login_as(page, teacher)

    page.goto(f"/protocol?code={code}")
    expect(page.locator("#need-login")).to_be_hidden(timeout=SLOW)
    page.click("#an-session")
    expect(page.locator("#an-weak table").first).to_be_visible(timeout=SLOW)
    assert overflow(page) <= 0

    page.goto("/students")
    expect(page.locator("#students > div").first).to_be_visible(timeout=SLOW)
    page.locator("#students > div").first.click()
    expect(page.locator("#card")).to_be_visible(timeout=SLOW)
    assert overflow(page) <= 0

    page.goto("/spaces")
    expect(page.locator("#spaces > div").first).to_be_visible(timeout=SLOW)
    page.locator("#spaces > div").first.get_by_role("button", name="Открыть").click()
    expect(page.locator("#pubs > div").first).to_be_visible(timeout=SLOW)
    page.locator("#pubs").get_by_role("button", name="Показать").click()
    expect(page.locator("#preview")).to_be_visible(timeout=SLOW)
    assert overflow(page) <= 0

    page.goto("/teacher")
    page.get_by_role("button", name=code).click()
    expect(page.locator("#registration-btn")).to_be_visible(timeout=SLOW)
    assert overflow(page) <= 0


def test_student_page_with_the_not_ready_controls_fits_a_phone(
    page: Page, browser, live_server, open_session
):
    session = open_session()
    other = browser.new_context(base_url=live_server)
    first = other.new_page()
    student_join(first, session["code"], "Первый")
    page.set_viewport_size(PHONE)
    student_join(page, session["code"], "Второй студент с очень длинным именем")
    expect(page.locator("#notready-btn")).to_be_visible(timeout=SLOW)
    assert overflow(page) <= 0
    page.click("#notready-btn")
    expect(page.locator("#ready-btn")).to_be_visible(timeout=SLOW)
    assert overflow(page) <= 0
    other.close()


def test_order_mode_controls_fit_a_phone(page: Page, browser, live_server, open_session):
    session = open_session(
        order_mode="lottery", ready_priority=True, ready_bonus_points=5, ready_skip_questions=1
    )
    code = session["code"]
    other = browser.new_context(base_url=live_server)
    other_page = other.new_page()
    other_page.set_viewport_size(PHONE)
    other_page.goto("/")
    other_page.fill("#code", code)
    other_page.fill("#name", "Студент с очень длинным именем и фамилией")
    expect(other_page.locator("#ready-choice")).to_be_visible(timeout=SLOW)
    assert overflow(other_page) <= 0
    other_page.check("#ready-now")
    other_page.click("#join-btn")
    expect(other_page.locator("#message")).to_contain_text("Вы в списке", timeout=SLOW)
    expect(other_page.locator("#readynow-btn")).to_be_visible()
    assert overflow(other_page) <= 0

    page.set_viewport_size(PHONE)
    login_as(page, session)
    page.goto("/teacher")
    expect(page.locator("#order-mode")).to_be_visible(timeout=SLOW)
    assert overflow(page) <= 0
    page.get_by_role("button", name=code).click()
    expect(page.locator("#lottery-btn")).to_be_visible(timeout=SLOW)
    page.click("#settings-box summary")
    expect(page.locator("#s-ready-bonus")).to_be_visible()
    assert overflow(page) <= 0
    other.close()


def test_budget_panel_and_time_fields_fit_a_phone(page: Page, api, open_session):
    teacher = open_session("Лимит на телефоне", duration_minutes=30)
    headers = teacher["headers"]
    assignment = api.post(
        "/assignments",
        headers=headers,
        json={"title": f"Работа {teacher['username']}", "questions_per_submission": 3},
    ).json()
    api.post(
        f"/assignments/{assignment['id']}/questions",
        headers=headers,
        json={"text": "Вопрос", "difficulty": 2},
    )
    api.put(
        f"/sessions/{teacher['code']}/assignment",
        headers=headers,
        json={"assignment_id": assignment["id"]},
    )
    for i in range(8):
        api.post(
            f"/sessions/{teacher['code']}/join",
            json={"name": f"Студент с длинным именем {i}", "token": f"phone-{i:03d}-" + "x" * 10},
        )
    page.set_viewport_size(PHONE)
    login_as(page, teacher)
    page.goto("/teacher")
    expect(page.locator("#limit-until")).to_be_visible(timeout=SLOW)
    assert overflow(page) <= 0
    page.get_by_role("button", name=teacher["code"]).click()
    expect(page.locator("#budget-offer")).to_be_visible(timeout=SLOW)
    assert overflow(page) <= 0
