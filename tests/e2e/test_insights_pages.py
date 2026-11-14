import os
import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_mobile_layout import PHONE, assert_fits
from tests.e2e.test_pages import SLOW
from tests.e2e.test_protocol_pages import login_as, prepare
from tests.e2e.test_ui_audit import contrast_violations, snapshot
from tests.e2e.test_weak_spots_pages import finish

pytestmark = pytest.mark.e2e


def snapshot_part(page, selector, name, scheme):
    shots = os.environ.get("UI_AUDIT_DIR")
    if shots:
        page.locator(selector).screenshot(path=f"{shots}/{name}-phone-{scheme}.png")


MARKS = [(5, 1), (4, 2), (5, 0), (4, 1), (3, 2), (5, 0), (2, 1), (4, 0)]


def with_results(api, open_session, count):
    teacher, assignment = prepare(api, open_session, questions=2)
    criteria = api.get(
        f"/assignments/{assignment['id']}/criteria", headers=teacher["headers"]
    ).json()
    for index, points in enumerate(MARKS[:count]):
        finish(api, teacher, criteria, index, points, pause=1.1 if index < 2 else 0)
    return teacher


def open_report(page: Page, teacher: dict) -> None:
    login_as(page, teacher)
    page.goto(f"/protocol?code={teacher['code']}")
    expect(page.locator("#need-login")).to_be_hidden(timeout=SLOW)
    page.click("#an-session")
    expect(page.locator("#an-main-box")).to_be_visible(timeout=SLOW)


def test_the_main_block_gives_short_conclusions_with_numbers_and_advice(
    page: Page, api, open_session, shot
):
    teacher = with_results(api, open_session, 8)
    open_report(page, teacher)

    cards = page.locator("#an-main-box .insight")
    assert 3 <= cards.count() <= 5
    expect(page.locator("#an-main-box h3")).to_have_text("Главное")
    for index in range(cards.count()):
        card = cards.nth(index)
        expect(card).to_contain_text("Что можно сделать:")
        assert any(ch.isdigit() for ch in card.locator(".what-text").inner_text())
        expect(card.locator(".tag")).to_have_text(re.compile("Обратите внимание|К сведению"))
    expect(page.locator("#an-main-box")).to_contain_text("«Защита»")
    snapshot(page, "report", "light", "desktop")
    page.click("#an-details summary")
    shot(page, "analytics-report")
    assert "Студент" not in page.locator("#an-main-box").inner_text()


def test_charts_are_svg_with_text_values_and_labels(page: Page, api, open_session):
    teacher = with_results(api, open_session, 8)
    open_report(page, teacher)

    charts = page.locator("#an-charts svg")
    expect(charts).to_have_count(3)
    for index in range(3):
        chart = charts.nth(index)
        assert chart.get_attribute("role") == "img"
        assert chart.get_attribute("aria-label")
    criteria = charts.nth(0)
    expect(criteria.locator("text").filter(has_text="Код")).to_have_count(1)
    assert "%" in criteria.text_content()
    expect(page.locator("#an-charts")).to_contain_text("Закрашенная полоса")
    assert page.locator("#an-charts script, #an-charts img, #an-charts iframe").count() == 0


def test_details_are_collapsed_and_use_plain_words(page: Page, api, open_session):
    teacher = with_results(api, open_session, 8)
    open_report(page, teacher)

    assert page.locator("#an-details").evaluate("node => node.open") is False
    expect(page.locator("#an-weak table").first).to_be_hidden()
    page.click("#an-details summary")
    expect(page.locator("#an-weak table").first).to_be_visible()
    text = page.locator("#an-details").inner_text()
    for jargon in ("Разброс", "средние 50%", "Агрегаты"):
        assert jargon not in text, jargon
    assert "обычно от" in text
    assert "Насколько оценки отличаются друг от друга" in text


def test_scarce_data_collapses_to_one_line_and_hides_the_charts(page: Page, api, open_session):
    teacher = with_results(api, open_session, 3)
    open_report(page, teacher)

    expect(page.locator("#an-main-note")).to_contain_text("Недостаточно данных (нужно не менее 5")
    expect(page.locator("#an-main-box .insight")).to_have_count(0)
    expect(page.locator("#an-charts svg")).to_have_count(0)


def test_retakes_and_quarters_show_one_line_when_there_is_too_little_data(
    page: Page, api, open_session
):
    teacher = with_results(api, open_session, 5)
    open_report(page, teacher)
    page.click("#an-details summary")

    expect(page.locator("#fair-attempts-note")).to_contain_text(
        "недостаточно данных (нужно не менее 5"
    )
    expect(page.locator("#peers-note")).to_contain_text("Сравнение с другими преподавателями")
    expect(page.get_by_role("heading", name="Первые попытки и пересдачи")).to_have_count(0)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_report_fits_a_phone_and_passes_the_contrast_check(
    page: Page, api, open_session, scheme
):
    teacher = with_results(api, open_session, 8)
    page.set_viewport_size(PHONE)
    page.emulate_media(color_scheme=scheme)
    open_report(page, teacher)
    expect(page.locator("#an-charts svg").first).to_be_visible()
    page.click("#an-details summary")

    assert_fits(page)
    assert contrast_violations(page) == []
    snapshot(page, "report", scheme, "phone")
    snapshot_part(page, "#an-main-box", "main", scheme)
    snapshot_part(page, "#an-charts", "charts", scheme)
