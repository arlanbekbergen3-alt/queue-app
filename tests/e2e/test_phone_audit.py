import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import SLOW, student_join

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
