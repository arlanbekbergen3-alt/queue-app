import os
from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page, expect

from tests.e2e.test_mobile_layout import assert_fits
from tests.e2e.test_pages import SLOW, student_join
from tests.e2e.test_protocol_pages import login_as, prepare

pytestmark = pytest.mark.e2e

SIZES = {"phone": {"width": 360, "height": 740}, "desktop": {"width": 1280, "height": 900}}
WIDE = {"width": 1920, "height": 1000}
SHOTS = os.environ.get("UI_AUDIT_DIR")


def contrast_violations(page: Page) -> list[str]:
    result = Axe().run(page, options={"runOnly": {"type": "rule", "values": ["color-contrast"]}})
    return [
        f"{node['html'][:90]} :: {node['any'][0]['message'] if node['any'] else ''}"
        for violation in result.response["violations"]
        for node in violation["nodes"]
    ]


def snapshot(page: Page, name: str, scheme: str, size: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}-{size}-{scheme}.png"), full_page=True)


def seed(api, open_session):
    teacher, assignment = prepare(api, open_session, questions=3, practice=True)
    headers = teacher["headers"]
    space = api.post("/spaces", headers=headers, json={"name": "Базы данных"}).json()
    api.post(
        f"/spaces/{space['id']}/publications",
        headers=headers,
        json={"assignment_id": assignment["id"]},
    )
    for n in range(3):
        api.post(
            f"/sessions/{teacher['code']}/join",
            json={"name": f"Студент {n}", "token": f"audit-{n}-" + "x" * 12},
        )
    api.post(f"/sessions/{teacher['code']}/next", headers=headers)
    return teacher, space


def open_page(page: Page, teacher: dict, name: str) -> None:
    code = teacher["code"]
    if name == "student":
        student_join(page, code, "Аудит")
        expect(page.locator("#status-badge")).to_be_visible(timeout=SLOW)
        return
    login_as(page, teacher)
    target = {
        "teacher": "/teacher",
        "protocol": f"/protocol?code={code}",
        "students": "/students",
        "spaces": "/spaces",
        "practice": f"/practice?code={code}",
    }[name]
    page.goto(target)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_every_page_passes_the_contrast_check_and_fits(page: Page, api, open_session, scheme, size):
    teacher, _ = seed(api, open_session)
    page.set_viewport_size(SIZES[size])
    page.emulate_media(color_scheme=scheme)
    problems: dict[str, list[str]] = {}
    for name in ("student", "teacher", "protocol", "students", "spaces", "practice"):
        open_page(page, teacher, name)
        found = contrast_violations(page)
        if found:
            problems[name] = found
        if size == "phone":
            assert_fits(page)
        snapshot(page, name, scheme, size)
    assert not problems, problems


@pytest.mark.parametrize("width", [1280, 1920])
def test_the_page_container_is_centered_on_wide_screens(page: Page, api, open_session, width):
    teacher, _ = seed(api, open_session)
    page.set_viewport_size({"width": width, "height": 900})
    for name in ("student", "teacher", "protocol", "students", "spaces", "practice"):
        open_page(page, teacher, name)
        left, right = page.evaluate(
            """() => {
                const box = document.querySelector("main").getBoundingClientRect();
                return [box.left, document.documentElement.clientWidth - box.right];
            }"""
        )
        assert abs(left - right) <= 1, (name, left, right)


def test_counters_use_the_full_word_in_the_right_form(page: Page, api, open_session):
    teacher, _ = seed(api, open_session)
    login_as(page, teacher)
    page.goto("/protocol")
    row = page.locator("#assignments > div").first
    expect(row).to_contain_text("3 вопроса", timeout=SLOW)
    expect(row).to_contain_text("критерия")
    expect(row).to_contain_text("1 сдача")
    for word in ("вопр.", "крит."):
        assert word not in row.inner_text()
    forms = page.evaluate(
        """() => [1, 2, 5, 11, 12, 14, 21, 22, 25, 101, 111].map((n) => counted(n, "вопрос", "вопроса", "вопросов"))"""
    )
    assert forms == [
        "1 вопрос",
        "2 вопроса",
        "5 вопросов",
        "11 вопросов",
        "12 вопросов",
        "14 вопросов",
        "21 вопрос",
        "22 вопроса",
        "25 вопросов",
        "101 вопрос",
        "111 вопросов",
    ]


def test_a_work_row_keeps_its_badges_inside_and_the_button_beside_or_below(
    page: Page, api, open_session
):
    teacher, _ = seed(api, open_session)
    login_as(page, teacher)
    for name, viewport in (("phone", SIZES["phone"]), ("desktop", SIZES["desktop"])):
        page.set_viewport_size(viewport)
        page.goto("/protocol")
        row = page.locator("#assignments > div").first
        expect(row).to_be_visible(timeout=SLOW)
        rows = page.evaluate(
            """() => {
                const row = document.querySelector("#assignments > div");
                const rect = (node) => node.getBoundingClientRect();
                const box = rect(row), button = rect(row.querySelector("button"));
                const badges = [...row.querySelectorAll(".badge")].map(rect);
                return { box: [box.left, box.right], button: [button.left, button.right, button.top, button.bottom],
                         badges: badges.map((b) => [b.left, b.right, b.top, b.bottom]) };
            }"""
        )
        assert rows["button"][1] <= rows["box"][1] + 1, name
        assert all(b[1] <= rows["box"][1] + 1 for b in rows["badges"]), name
        if name == "desktop":
            middle = (rows["button"][2] + rows["button"][3]) / 2
            top = min(b[2] for b in rows["badges"])
            assert abs(middle - ((top + rows["button"][3]) / 2)) < 80
        else:
            assert rows["button"][2] >= max(b[3] for b in rows["badges"]) - 1
