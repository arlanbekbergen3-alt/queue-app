import re

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


def wide_elements(page: Page) -> list[str]:
    return page.evaluate(
        """() => {
            const limit = document.documentElement.clientWidth + 0.5;
            return [...document.querySelectorAll("body *")]
                .filter((node) => node.getBoundingClientRect().right > limit)
                .slice(0, 8)
                .map((node) => node.tagName.toLowerCase() + (node.id ? "#" + node.id : "")
                    + (node.className && typeof node.className === "string" ? "." + node.className.split(" ").join(".") : "")
                    + ":" + Math.round(node.getBoundingClientRect().right));
        }"""
    )


def assert_fits(page: Page) -> None:
    assert overflow(page) <= 0, wide_elements(page)


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
    expect(page.locator("#an-details")).to_be_visible(timeout=SLOW)
    page.locator("#an-details").evaluate("node => { node.open = true; }")
    expect(page.locator("#an-weak table").first).to_be_visible(timeout=SLOW)
    assert_fits(page)

    page.goto("/students")
    expect(page.locator("#students > div").first).to_be_visible(timeout=SLOW)
    page.locator("#students > div").first.click()
    expect(page.locator("#card")).to_be_visible(timeout=SLOW)
    assert_fits(page)

    page.goto("/spaces")
    expect(page.locator("#spaces > div").first).to_be_visible(timeout=SLOW)
    page.locator("#spaces > div").first.get_by_role("button", name="Открыть").click()
    expect(page.locator("#pubs > div").first).to_be_visible(timeout=SLOW)
    page.locator("#pubs").get_by_role("button", name="Показать").click()
    expect(page.locator("#preview")).to_be_visible(timeout=SLOW)
    assert_fits(page)

    page.goto("/teacher")
    page.get_by_role("button", name=code).click()
    expect(page.locator("#registration-btn")).to_be_visible(timeout=SLOW)
    assert_fits(page)


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
    assert_fits(page)
    page.click("#notready-btn")
    expect(page.locator("#ready-btn")).to_be_visible(timeout=SLOW)
    assert_fits(page)
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
    assert_fits(other_page)
    other_page.check("#ready-now")
    other_page.click("#join-btn")
    expect(other_page.locator("#message")).to_contain_text("Вы в списке", timeout=SLOW)
    expect(other_page.locator("#readynow-btn")).to_be_visible()
    assert_fits(other_page)

    page.set_viewport_size(PHONE)
    login_as(page, session)
    page.goto("/teacher")
    expect(page.locator("#order-mode")).to_be_visible(timeout=SLOW)
    assert_fits(page)
    page.get_by_role("button", name=code).click()
    expect(page.locator("#lottery-btn")).to_be_visible(timeout=SLOW)
    page.click("#settings-box summary")
    expect(page.locator("#s-ready-bonus")).to_be_visible()
    assert_fits(page)
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
    assert_fits(page)
    page.get_by_role("button", name=teacher["code"]).click()
    expect(page.locator("#budget-offer")).to_be_visible(timeout=SLOW)
    assert_fits(page)


def test_start_panel_and_work_panel_fit_a_phone(
    page: Page, browser, live_server, api, open_session
):
    teacher, _ = prepare(api, open_session, questions=4)
    other = browser.new_context(base_url=live_server)
    other_page = other.new_page()
    student_join(other_page, teacher["code"], "Студент с очень длинным именем и фамилией")
    page.set_viewport_size(PHONE)
    login_as(page, teacher)
    page.goto("/teacher")
    expect(page.locator("#mode")).to_be_visible(timeout=SLOW)
    assert_fits(page)
    page.click("#create-btn")
    expect(page.locator("#queue-code")).to_be_visible(timeout=SLOW)
    page.click("#start-panel summary")
    expect(page.locator("#start-qr")).to_have_attribute("src", re.compile(r"qr\.svg"))
    expect(page.locator("#start-qr")).to_have_js_property("complete", True)
    assert page.locator("#start-qr").evaluate("node => node.naturalWidth") > 0
    assert_fits(page)
    page.click("#start-panel summary")
    page.get_by_role("button", name=teacher["code"]).click()
    expect(page.locator("#entries")).to_contain_text("Студент с очень", timeout=SLOW)
    page.click("#next-btn")
    expect(page.locator("#work-card")).to_be_visible(timeout=SLOW)
    expect(page.locator("#work-body .crit")).to_have_count(2)
    assert_fits(page)
    other.close()


def test_bulk_import_presets_and_quick_join_fit_a_phone(
    page: Page, browser, live_server, api, open_session
):
    teacher = open_session("Телефон: ввод")
    api.post(
        "/assignments",
        headers=teacher["headers"],
        json={"title": "Очень длинное название работы для проверки"},
    )
    page.set_viewport_size(PHONE)
    login_as(page, teacher)
    page.goto("/protocol")
    page.locator("#assignments > div").first.get_by_role("button", name="Открыть").click()
    expect(page.locator("#c-presets")).to_be_visible(timeout=SLOW)
    page.click("#preset-coursework")
    expect(page.locator("#criteria > div").first).to_be_visible(timeout=SLOW)
    page.fill("#qb-text", "1. " + "очень длинный вопрос " * 12 + "\n2. Второй вопрос?")
    page.click("#qb-preview")
    expect(page.locator("#qb-list .bulk-row").first).to_be_visible(timeout=SLOW)
    assert_fits(page)

    other = browser.new_context(base_url=live_server)
    student = other.new_page()
    student.set_viewport_size(PHONE)
    student.add_init_script(
        "try { localStorage.setItem('queue-name', 'Студент с очень длинным именем и фамилией'); } catch (e) {}"
    )
    student.goto(f"/?code={teacher['code']}")
    expect(student.locator("#quick-join")).to_be_visible(timeout=SLOW)
    assert_fits(student)
    other.close()


def test_presence_code_and_field_fit_a_phone(page: Page, browser, live_server, open_session):
    teacher = open_session("Телефон: присутствие", presence_check="rotating_code")
    login_as(page, teacher)
    page.set_viewport_size(PHONE)
    page.goto("/teacher")
    page.get_by_role("button", name=teacher["code"]).click()
    expect(page.locator("#presence-digits")).to_have_text(
        re.compile(r"^\d{3} \d{3}$"), timeout=SLOW
    )
    page.wait_for_function("document.getElementById('presence-qr').naturalWidth > 0")
    assert_fits(page)
    other = browser.new_context(base_url=live_server)
    student = other.new_page()
    student.set_viewport_size(PHONE)
    student.goto(f"/?code={teacher['code']}")
    expect(student.locator("#presence-box")).to_be_visible(timeout=SLOW)
    assert_fits(student)
    other.close()


def test_roster_levels_and_reopen_fit_a_phone_screen(
    page: Page, browser, live_server, api, open_session
):
    teacher, assignment = prepare(api, open_session, questions=3)
    headers = teacher["headers"]
    names = "\n".join(f"Очень длинная Фамилия-Двойная Имя {i}" for i in range(24))
    api.put("/roster", headers=headers, json={"group": "ИС-22", "text": names})
    first = api.get(f"/assignments/{assignment['id']}/criteria", headers=headers).json()[0]
    api.put(
        f"/assignments/{assignment['id']}/criteria/{first['id']}/levels",
        headers=headers,
        json={
            "levels": [
                {"points": 0, "text": "Нет ответа вообще, студент не смог начать решение"},
                {"points": 3, "text": "Частично верно, но есть значительные неточности в расчётах"},
                {"points": 5, "text": "Верно и полно, с пояснениями"},
            ]
        },
    )
    session = api.post(
        "/sessions",
        headers=headers,
        json={"title": "Зачёт", "group": "ИС-22", "session_mode": "protocol", "roster_only": True},
    ).json()
    api.put(
        f"/sessions/{session['code']}/assignment",
        headers=headers,
        json={"assignment_id": assignment["id"]},
    )

    context = browser.new_context(base_url=live_server, viewport=PHONE)
    student = context.new_page()
    student.goto(f"/?code={session['code']}")
    expect(student.locator("#roster-list button").first).to_be_visible(timeout=SLOW)
    assert_fits(student)
    student.locator("#roster-list button").first.click()
    student.click("#join-btn")
    expect(student.locator("#status-badge")).to_have_text("В очереди", timeout=SLOW)
    context.close()

    page.set_viewport_size(PHONE)
    login_as(page, teacher)
    page.goto("/teacher")
    page.fill("#group", "ИС-22")
    expect(page.locator("#roster-only-row")).to_be_visible(timeout=SLOW)
    assert_fits(page)
    page.get_by_role("button", name=session["code"]).click()
    page.click("#next-btn")
    expect(page.locator("#work-body .lvl").first).to_be_visible(timeout=SLOW)
    assert_fits(page)
    page.once("dialog", lambda dialog: dialog.accept())
    page.click("#close-btn")
    expect(page.locator("#reopen-btn")).to_be_visible(timeout=SLOW)
    assert_fits(page)

    page.goto("/students")
    expect(page.locator("#r-groups")).to_contain_text("ИС-22", timeout=SLOW)
    page.locator("#r-groups").get_by_role("button", name="Показать").click()
    expect(page.locator("#r-text")).not_to_have_value("")
    assert_fits(page)

    page.goto("/protocol")
    page.locator("#assignments").get_by_role("button", name="Открыть").first.click()
    page.locator("#criteria").get_by_role("button", name="Уровни").first.click()
    expect(page.locator("#criteria textarea").first).to_be_visible(timeout=SLOW)
    assert_fits(page)
