import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import student_join
from tests.e2e.test_protocol_pages import login_as, prepare

pytestmark = pytest.mark.e2e

SLOW = 20_000


def grade_current(api, teacher, code, points):
    api.post(f"/sessions/{code}/next", headers=teacher["headers"])
    current = api.get(f"/sessions/{code}/submissions/current", headers=teacher["headers"]).json()
    criteria = api.get(
        f"/assignments/{current_assignment(api, teacher)}/criteria", headers=teacher["headers"]
    ).json()
    scores = [{"criterion_id": c["id"], "points": p} for c, p in zip(criteria, points, strict=True)]
    done = api.post(
        f"/submissions/{current['id']}/complete",
        headers=teacher["headers"],
        json={"scores": scores},
    )
    assert done.status_code == 200, done.text


def current_assignment(api, teacher):
    return api.get("/assignments", headers=teacher["headers"]).json()[0]["id"]


def test_retake_history_for_the_teacher_card_and_for_the_student(
    page: Page, browser, live_server, api, open_session
):
    teacher, assignment = prepare(api, open_session, questions=6)
    first_code = teacher["code"]
    second = api.post("/sessions", headers=teacher["headers"], json={"title": "Пересдача"}).json()
    api.put(
        f"/sessions/{second['code']}/assignment",
        headers=teacher["headers"],
        json={"assignment_id": assignment["id"]},
    )
    student = browser.new_context(base_url=live_server)
    student_page = student.new_page()

    student_join(student_page, first_code, "Алёна Фёдорова")
    expect(student_page.locator("#status-badge")).to_have_text("В очереди", timeout=SLOW)
    grade_current(api, teacher, first_code, (3, 2))
    expect(student_page.locator("#result")).to_contain_text("Итог: 5 из 10", timeout=SLOW)

    student_page.click("#again-btn")
    student_join(student_page, second["code"], "алена  федорова")
    expect(student_page.locator("#status-badge")).to_have_text("В очереди", timeout=SLOW)
    grade_current(api, teacher, second["code"], (5, 4))
    expect(student_page.locator("#result")).to_contain_text("Итог: 9 из 10", timeout=SLOW)
    expect(student_page.locator("#result")).to_contain_text("попытка 2", timeout=SLOW)
    expect(student_page.locator("#result")).to_contain_text("+40 п.п.")

    login_as(page, teacher)
    page.goto("/students")
    row = page.locator("#students > div", has_text="Алёна Фёдорова")
    expect(row).to_contain_text("сдач: 2", timeout=SLOW)
    row.click()
    card = page.locator("#card")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Работа")
    expect(card).to_contain_text("50%")
    expect(card).to_contain_text("90%")
    expect(card).to_contain_text("+40 п.п.")
    expect(card).to_contain_text("Какие вопросы выпадали")
    assert page.locator("#card tbody tr").count() == 2
    student.close()


def test_teacher_can_merge_two_records_on_the_students_page(
    page: Page, browser, live_server, api, open_session
):
    teacher, assignment = prepare(api, open_session, questions=6)
    second = api.post("/sessions", headers=teacher["headers"], json={"title": "Вторая"}).json()
    api.put(
        f"/sessions/{second['code']}/assignment",
        headers=teacher["headers"],
        json={"assignment_id": assignment["id"]},
    )
    for code, name in ((teacher["code"], "Вася Пупкин"), (second["code"], "Василий Пупкин")):
        context = browser.new_context(base_url=live_server)
        p = context.new_page()
        student_join(p, code, name)
        expect(p.locator("#status-badge")).to_have_text("В очереди", timeout=SLOW)
        grade_current(api, teacher, code, (4, 4))
        context.close()

    login_as(page, teacher)
    page.goto("/students")
    expect(page.locator("#students > div")).to_have_count(2, timeout=SLOW)
    page.locator("#students > div", has_text="Вася Пупкин").click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.click("#c-merge")
    expect(page.locator("#c-err")).to_have_text("Записи объединены", timeout=SLOW)
    expect(page.locator("#students > div")).to_have_count(1)
    expect(page.locator("#c-meta")).to_contain_text("Вася Пупкин")
    assert page.locator("#card tbody tr").count() == 2
