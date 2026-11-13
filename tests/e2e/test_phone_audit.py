import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_mobile_layout import assert_fits
from tests.e2e.test_pages import SLOW, student_join
from tests.e2e.test_protocol_pages import login_as, prepare

pytestmark = pytest.mark.e2e


def test_a_queue_only_student_never_asks_for_a_submission_that_does_not_exist(
    page: Page, api, open_session
):
    session = open_session("Без оценок", session_mode="queue_only")
    code = session["code"]
    failed, errors = [], []
    page.on("response", lambda r: failed.append(r.url) if r.status >= 400 else None)
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    student_join(page, code, "Анна Иванова")
    expect(page.locator("#queue-view")).to_be_visible(timeout=SLOW)
    called = api.post(f"/sessions/{code}/next", headers=session["headers"]).json()
    api.post(f"/sessions/{code}/entries/{called['id']}/done", headers=session["headers"])
    expect(page.locator("#message")).to_contain_text("Сдача завершена", timeout=SLOW)
    page.wait_for_timeout(1500)
    assert failed == [] and errors == []


IPHONE_SE = {"width": 375, "height": 667}


def test_the_current_submission_hint_wraps_on_an_iphone_se(page: Page, api, open_session):
    teacher, _ = prepare(api, open_session, questions=4)
    api.post(
        f"/sessions/{teacher['code']}/join",
        json={"name": "Анна Иванова", "token": "phone-audit-hint-0001"},
    )
    api.post(f"/sessions/{teacher['code']}/next", headers=teacher["headers"])
    page.set_viewport_size(IPHONE_SE)
    login_as(page, teacher)
    page.goto(f"/protocol?code={teacher['code']}")
    expect(page.locator("#current .timer")).to_be_visible(timeout=SLOW)
    assert_fits(page)
