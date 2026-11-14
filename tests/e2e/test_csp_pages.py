import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_protocol_pages import login_as

pytestmark = pytest.mark.e2e

PAGES = ("/", "/teacher", "/protocol", "/students", "/spaces", "/practice")


@pytest.mark.parametrize("path", PAGES)
def test_pages_run_under_the_policy_and_an_injected_script_does_not(page: Page, teacher_api, path):
    teacher = teacher_api()
    login_as(page, teacher)
    messages: list[str] = []
    page.on("console", lambda message: messages.append(message.text))
    page.on("pageerror", lambda error: messages.append(str(error)))

    page.goto(path)
    page.wait_for_load_state("networkidle")
    page.evaluate(
        """() => {
            const script = document.createElement("script");
            script.textContent = "window.injected = true";
            document.body.append(script);
        }"""
    )

    assert page.evaluate("window.injected === undefined")
    assert any("Content Security Policy" in text for text in messages)
    assert not [
        text
        for text in messages
        if "Content Security Policy" in text
        and "window.injected" not in text
        and "inline script" not in text
    ]
    expect(page.locator("body")).not_to_be_empty()
