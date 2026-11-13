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


SMALL_TARGETS = """() => {
    const visible = (n) => {
        const r = n.getBoundingClientRect();
        const s = getComputedStyle(n);
        return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
    };
    const label = (n) =>
        n.tagName.toLowerCase() + (n.id ? "#" + n.id : "") + ' "' +
        (n.innerText || n.value || n.placeholder || "").trim().slice(0, 24) + '" h=' +
        Math.round(n.getBoundingClientRect().height);
    return [...document.querySelectorAll("button, a[href], summary, select, input:not([type=hidden]), textarea")]
        .filter(visible)
        .filter((n) => !(n.tagName === "INPUT" && ["checkbox", "radio"].includes(n.type)))
        .filter((n) => n.getBoundingClientRect().height < 40)
        .map(label);
}"""


def test_every_control_is_at_least_40px_tall_on_a_phone(page: Page, api, open_session):
    teacher, assignment = prepare(api, open_session, questions=4)
    for n in range(3):
        api.post(
            f"/sessions/{teacher['code']}/join",
            json={"name": f"Студент {n}", "token": f"phone-targets-{n:04d}-xxxx"},
        )
    api.post(f"/sessions/{teacher['code']}/next", headers=teacher["headers"])
    page.set_viewport_size({"width": 360, "height": 740})
    login_as(page, teacher)
    small = {}

    page.goto("/teacher")
    expect(page.locator("#app-view")).to_be_visible(timeout=SLOW)
    small["teacher-wizard"] = page.evaluate(SMALL_TARGETS)
    page.get_by_role("button", name=teacher["code"]).click()
    expect(page.locator("#work-card")).to_be_visible(timeout=SLOW)
    expect(page.locator("#entries tr", has_text="Студент 2")).to_be_visible(timeout=SLOW)
    small["teacher-queue"] = page.evaluate(SMALL_TARGETS)

    page.goto("/protocol")
    page.locator("#assignments > div").first.get_by_role("button", name="Открыть").click()
    expect(page.locator("#a-card")).to_be_visible(timeout=SLOW)
    small["protocol-assignment"] = page.evaluate(SMALL_TARGETS)
    page.goto(f"/protocol?code={teacher['code']}")
    expect(page.locator("#current .timer")).to_be_visible(timeout=SLOW)
    small["protocol-session"] = page.evaluate(SMALL_TARGETS)

    page.goto("/students")
    expect(page.locator("#students > div").first).to_be_visible(timeout=SLOW)
    small["students"] = page.evaluate(SMALL_TARGETS)
    page.goto("/spaces")
    expect(page.locator("#s-create")).to_be_visible(timeout=SLOW)
    small["spaces"] = page.evaluate(SMALL_TARGETS)

    page.goto(f"/?code={teacher['code']}")
    small["student-join"] = page.evaluate(SMALL_TARGETS)
    assert {name: items for name, items in small.items() if items} == {}
