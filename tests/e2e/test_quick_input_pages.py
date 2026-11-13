import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import SLOW
from tests.e2e.test_protocol_pages import login_as, prepare

pytestmark = pytest.mark.e2e


def open_assignment(page: Page, teacher: dict, api, title: str):
    api.post("/assignments", headers=teacher["headers"], json={"title": title})
    login_as(page, teacher)
    page.goto("/protocol")
    row = page.locator("#assignments > div", has_text=title)
    expect(row).to_have_count(1, timeout=SLOW)
    row.get_by_role("button", name="Открыть").click()
    expect(page.locator("#a-card")).to_be_visible(timeout=SLOW)


def test_pasted_list_is_cleaned_previewed_trimmed_and_added_in_one_click(
    page: Page, api, open_session
):
    teacher = open_session("Вставка")
    open_assignment(page, teacher, api, "Банк для вставки")
    page.fill(
        "#qb-text",
        "1. Что такое индекс?\n2) Чем JOIN отличается от UNION?\n\n- Что такое индекс?\n"
        "• Зачем нужна транзакция?\n" + "я" * 1001,
    )
    page.click("#qb-preview")
    rows = page.locator("#qb-list .bulk-row")
    expect(rows).to_have_count(5, timeout=SLOW)
    expect(rows.nth(0)).to_contain_text("Что такое индекс?")
    expect(rows.nth(0)).to_contain_text("новый")
    expect(rows.nth(2)).to_contain_text("повтор")
    expect(rows.nth(4)).to_contain_text("длиннее 1000")
    expect(page.locator("#qb-count")).to_have_text("Будет добавлено: 3 из 5")
    expect(rows.nth(2).locator("input")).to_be_disabled()

    rows.nth(1).locator("input").uncheck()
    expect(page.locator("#qb-count")).to_have_text("Будет добавлено: 2 из 5")
    page.click("#qb-add")
    expect(page.locator("#qb-err")).to_have_text("Добавлено вопросов: 2", timeout=SLOW)
    expect(page.locator("#questions > div")).to_have_count(2)
    expect(page.locator("#questions")).to_contain_text("Зачем нужна транзакция?")
    expect(page.locator("#questions")).not_to_contain_text("JOIN")
    expect(page.locator("#qb-box")).to_be_hidden()


def test_ready_criteria_sets_are_added_with_one_click_and_stay_editable(
    page: Page, api, open_session, shot
):
    teacher = open_session("Наборы")
    open_assignment(page, teacher, api, "Работа с набором")
    for key, title in (
        ("practice", "Практическая работа"),
        ("coursework", "Курсовая работа"),
        ("oral", "Устный ответ"),
    ):
        expect(page.locator(f"#preset-{key}")).to_have_text(title)
    page.click("#preset-oral")
    expect(page.locator("#criteria > div")).to_have_count(4, timeout=SLOW)
    expect(page.locator("#criteria")).to_contain_text("Полнота ответа")
    page.click("#preset-oral")
    expect(page.locator("#criteria > div")).to_have_count(4)
    page.click("#preset-practice")
    expect(page.locator("#criteria > div")).to_have_count(7, timeout=SLOW)
    page.fill("#c-name", "Своя строка")
    page.click("#c-add")
    expect(page.locator("#criteria > div")).to_have_count(8, timeout=SLOW)
    page.fill("#qb-text", "1. Что такое индекс?" + chr(10) + "2. Зачем нужна транзакция?")
    page.click("#qb-preview")
    expect(page.locator("#qb-list .bulk-row")).to_have_count(2, timeout=SLOW)
    page.set_viewport_size({"width": 1180, "height": 860})
    shot(page, "protocol-presets-and-bulk")


def test_protocol_grading_form_has_score_buttons_and_a_max_button(page: Page, api, open_session):
    teacher, _ = prepare(api, open_session, questions=4)
    api.post(
        f"/sessions/{teacher['code']}/join",
        json={"name": "Анна Иванова", "token": "buttons-form-token-0001"},
    )
    api.post(f"/sessions/{teacher['code']}/next", headers=teacher["headers"])
    login_as(page, teacher)
    page.goto(f"/protocol?code={teacher['code']}")
    form = page.locator("#current")
    expect(form.locator(".pts")).to_have_count(2, timeout=SLOW)
    form.locator(".pts").first.get_by_role("button", name="3", exact=True).click()
    expect(form.locator(".pts").first.locator("button.on")).to_have_text("3")
    expect(form.locator("input[type=number]").first).to_have_value("3")
    form.get_by_role("button", name="Всё на максимум").click()
    expect(form.locator("input[type=number]").first).to_have_value("5")
    expect(form.locator("input[type=number]").nth(1)).to_have_value("5")
    form.get_by_role("button", name="Завершить сдачу").click()
    expect(page.locator("#done")).to_contain_text("10/10", timeout=SLOW)


def test_a_returning_student_joins_with_one_click_and_a_blocked_storage_does_not_break_the_page(
    page: Page, browser, live_server, open_session
):
    session = open_session()
    page.add_init_script("try { localStorage.setItem('queue-name', 'Анна Иванова'); } catch (e) {}")
    page.goto(f"/?code={session['code']}")
    quick = page.locator("#quick-join")
    expect(quick).to_have_text("Войти как Анна Иванова", timeout=SLOW)
    quick.click()
    expect(page.locator("#queue-view")).to_be_visible(timeout=SLOW)
    expect(page.locator("#position")).to_have_text("1")

    blocked_ctx = browser.new_context(base_url=live_server)
    blocked = blocked_ctx.new_page()
    errors = []
    blocked.on("pageerror", lambda error: errors.append(str(error)))
    blocked.add_init_script(
        "Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });"
    )
    blocked.goto(f"/?code={session['code']}")
    expect(blocked.locator("#join-view")).to_be_visible(timeout=SLOW)
    expect(blocked.locator("#quick-join")).to_be_hidden()
    blocked.fill("#name", "Борис Петров")
    expect(blocked.locator("#quick-join")).to_have_text("Войти как Борис Петров")
    blocked.click("#join-btn")
    expect(blocked.locator("#queue-view")).to_be_visible(timeout=SLOW)
    assert errors == []
    blocked_ctx.close()
