import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

SLOW = 20_000


def login_as(page: Page, teacher: dict) -> None:
    token = teacher["headers"]["Authorization"].removeprefix("Bearer ")
    page.add_init_script(
        f"try {{ sessionStorage.setItem('queue-teacher-token', '{token}'); }} catch (e) {{}}"
    )


def prepare(api, open_session, questions=4, practice=False):
    teacher = open_session("Протокол")
    headers = teacher["headers"]
    assignment = api.post(
        "/assignments",
        headers=headers,
        json={
            "title": f"Работа {teacher['username']}",
            "questions_per_submission": 2,
            "practice_enabled": practice,
        },
    ).json()
    for i in range(questions):
        api.post(
            f"/assignments/{assignment['id']}/questions",
            headers=headers,
            json={"text": f"Вопрос номер {i}", "difficulty": 2},
        )
    for name in ("Код", "Защита"):
        api.post(
            f"/assignments/{assignment['id']}/criteria",
            headers=headers,
            json={"name": name, "max_points": 5},
        )
    api.put(
        f"/sessions/{teacher['code']}/assignment",
        headers=headers,
        json={"assignment_id": assignment["id"]},
    )
    return teacher, assignment


def test_teacher_grades_a_submission_and_student_sees_the_breakdown(
    page: Page, browser, live_server, api, open_session
):
    teacher, _ = prepare(api, open_session)
    student = browser.new_context(base_url=live_server)
    student_page = student.new_page()
    student_page.goto("/")
    student_page.fill("#code", teacher["code"])
    student_page.fill("#name", "Анна Иванова")
    student_page.click("#join-btn")
    expect(student_page.locator("#status-badge")).to_have_text("В очереди", timeout=SLOW)

    api.post(f"/sessions/{teacher['code']}/next", headers=teacher["headers"])

    login_as(page, teacher)
    page.goto(f"/protocol?code={teacher['code']}")
    current = page.locator("#current")
    expect(current).to_contain_text("Талон №1", timeout=SLOW)
    expect(current).to_contain_text("Вопрос номер")
    expect(current).to_contain_text("это подсказка, не ограничение")

    inputs = current.locator("input[type=number]")
    inputs.nth(0).fill("4")
    inputs.nth(1).fill("3")
    current.get_by_role("button", name="Завершить сдачу").click()
    expect(page.locator("#done")).to_contain_text("завершена", timeout=SLOW)
    expect(page.locator("#done")).to_contain_text("7/10")

    expect(student_page.locator("#result")).to_contain_text("Итог: 7 из 10", timeout=SLOW)
    expect(student_page.locator("#result")).to_contain_text("Код")
    expect(student_page.locator("#result")).to_contain_text("Ваши вопросы")
    student.close()


def test_teacher_corrects_a_grade_with_a_reason_and_sees_the_journal(page: Page, api, open_session):
    teacher, _ = prepare(api, open_session)
    api.post(
        "/sessions/" + teacher["code"] + "/join",
        json={"name": "Борис", "token": "b" * 20},
    )
    api.post(f"/sessions/{teacher['code']}/next", headers=teacher["headers"])
    login_as(page, teacher)
    page.goto(f"/protocol?code={teacher['code']}")
    current = page.locator("#current")
    expect(current).to_contain_text("Талон №1", timeout=SLOW)
    inputs = current.locator("input[type=number]")
    inputs.nth(0).fill("2")
    inputs.nth(1).fill("2")
    current.get_by_role("button", name="Завершить сдачу").click()
    expect(page.locator("#done")).to_contain_text("2/", timeout=SLOW)

    page.locator("#done").get_by_role("button", name="Править").click()
    page.locator("#done input[type=number]").first.fill("5")
    page.get_by_role("button", name="Сохранить правку").click()
    expect(page.locator("#done .err")).to_contain_text("Причина: минимум 3 символа")

    page.get_by_placeholder("Причина правки (обязательно)").fill("Пересмотр защиты")
    page.get_by_role("button", name="Сохранить правку").click()
    expect(page.locator("#done")).to_contain_text("7/", timeout=SLOW)

    page.locator("#done").get_by_role("button", name="Журнал").click()
    expect(page.locator("#audit")).to_contain_text("2 → 5")
    expect(page.locator("#audit")).to_contain_text("причина: Пересмотр защиты")


def test_teacher_builds_an_assignment_in_the_browser(page: Page, teacher_api):
    teacher = teacher_api()
    login_as(page, teacher)
    page.goto("/protocol")
    page.fill("#a-title", "Лабораторная из браузера")
    page.click("#a-create")
    expect(page.locator("#a-name")).to_have_text("Лабораторная из браузера", timeout=SLOW)

    page.click("#c-add")
    expect(page.locator("#c-err")).to_contain_text("Название критерия: минимум 1 символ")
    page.fill("#c-name", "Код")
    page.click("#c-add")
    expect(page.locator("#criteria")).to_contain_text("максимум 5")

    page.fill("#q-text", "Что такое индекс?")
    page.click("#q-add")
    expect(page.locator("#questions")).to_contain_text("Что такое индекс?")
    page.fill("#q-text", "Что такое индекс?")
    page.click("#q-add")
    expect(page.locator("#q-err")).to_have_text("Такой вопрос уже есть")


def test_ai_draft_shows_a_clear_message_when_the_model_server_is_unreachable(
    page: Page, api, open_session
):
    teacher, assignment = prepare(api, open_session)
    login_as(page, teacher)
    page.goto("/protocol")
    page.get_by_role("button", name="Открыть").first.click()
    expect(page.locator("#ai-badge")).to_contain_text("модель:", timeout=SLOW)
    page.fill("#ai-source", "Лабораторная работа: реализовать очередь на сдачу с блокировками.")
    page.click("#ai-start")
    expect(page.locator("#ai-err")).to_contain_text("Модель недоступна", timeout=SLOW)
    expect(page.locator("#ai-draft")).to_be_hidden()
    questions = api.get(f"/assignments/{assignment['id']}/questions", headers=teacher["headers"])
    assert len(questions.json()) == 4


def test_practice_page_falls_back_to_self_check_when_the_helper_is_unreachable(
    page: Page, api, open_session
):
    teacher, _ = prepare(api, open_session, practice=True)
    page.goto(f"/practice?code={teacher['code']}")
    expect(page.locator("#banner")).to_contain_text("не официальная оценка", timeout=SLOW)
    page.locator(".q").first.click()
    page.fill("#answer", "Индекс ускоряет поиск по таблице.")
    page.click("#check")
    expect(page.locator("#check-err")).to_have_text("Помощник недоступен", timeout=SLOW)
    expect(page.locator("#selfcheck")).to_be_visible()
    expect(page.locator("#criteria")).to_contain_text("Код")
    expect(page.locator("#result")).to_be_empty()


def test_student_sees_the_practice_link_only_when_the_teacher_enabled_it(
    page: Page, browser, live_server, api, open_session
):
    teacher, _ = prepare(api, open_session, practice=True)
    page.goto("/")
    page.fill("#code", teacher["code"])
    page.fill("#name", "Вера")
    page.click("#join-btn")
    expect(page.locator("#practice-link")).to_be_visible(timeout=SLOW)

    plain = open_session("Без тренировки")
    other = browser.new_context(base_url=live_server)
    p2 = other.new_page()
    p2.goto("/")
    p2.fill("#code", plain["code"])
    p2.fill("#name", "Глеб")
    p2.click("#join-btn")
    expect(p2.locator("#status-badge")).to_have_text("В очереди", timeout=SLOW)
    expect(p2.locator("#practice-link")).to_be_hidden()
    other.close()


def test_analytics_block_says_when_there_is_too_little_data(page: Page, api, open_session):
    teacher, _ = prepare(api, open_session)
    api.post(f"/sessions/{teacher['code']}/join", json={"name": "Дима", "token": "d" * 20})
    api.post(f"/sessions/{teacher['code']}/next", headers=teacher["headers"])
    login_as(page, teacher)
    page.goto(f"/protocol?code={teacher['code']}")
    current = page.locator("#current")
    expect(current).to_contain_text("Талон №1", timeout=SLOW)
    inputs = current.locator("input[type=number]")
    inputs.nth(0).fill("5")
    inputs.nth(1).fill("5")
    current.get_by_role("button", name="Завершить сдачу").click()
    expect(page.locator("#done")).to_contain_text("10/10", timeout=SLOW)
    page.click("#an-session")
    expect(page.locator("#an-out")).to_contain_text("Завершённых сдач: 1")
    expect(page.locator("#an-out")).to_contain_text("Мало данных")
    expect(page.locator("#an-out")).not_to_contain_text("Дима")


def test_ai_draft_survives_a_failed_status_poll_and_shows_the_counter(
    page: Page, api, open_session
):
    teacher, _ = prepare(api, open_session)
    login_as(page, teacher)
    page.goto("/protocol")
    page.get_by_role("button", name="Открыть").first.click()
    expect(page.locator("#ai-badge")).to_contain_text("модель:", timeout=SLOW)
    text = "Лабораторная работа: реализовать очередь на сдачу с блокировками."
    page.fill("#ai-source", text)
    expect(page.locator("#ai-count")).to_contain_text(f"{len(text)} / 12000", timeout=SLOW)

    failed = []

    def break_first_poll(route):
        if failed:
            route.continue_()
            return
        failed.append(True)
        route.abort()

    page.route("**/ai/jobs/*", break_first_poll)
    page.click("#ai-start")
    expect(page.locator("#ai-err")).to_contain_text("Модель недоступна", timeout=SLOW)
    assert failed
    expect(page.locator("#ai-start")).to_be_enabled()


def test_level_descriptions_are_shown_while_grading_in_both_screens(page: Page, api, open_session):
    teacher, assignment = prepare(api, open_session)
    headers = teacher["headers"]
    criteria = api.get(f"/assignments/{assignment['id']}/criteria", headers=headers).json()
    api.put(
        f"/assignments/{assignment['id']}/criteria/{criteria[0]['id']}/levels",
        headers=headers,
        json={
            "levels": [{"points": 0, "text": "Нет ответа"}, {"points": 5, "text": "Верно и полно"}]
        },
    )
    joined = api.post(
        "/sessions/" + teacher["code"] + "/join", json={"name": "Вера", "token": "v" * 20}
    )
    called = api.post(f"/sessions/{teacher['code']}/next", headers=headers)
    assert joined.status_code == 200 and called.status_code == 200, (joined.text, called.text)

    login_as(page, teacher)
    page.goto(f"/protocol?code={teacher['code']}")
    current = page.locator("#current")
    expect(current).to_contain_text("Талон №1", timeout=SLOW)
    expect(current).to_contain_text("0 — Нет ответа")
    expect(current).to_contain_text("5 — Верно и полно")

    page.goto("/protocol")
    page.locator("#assignments").get_by_role("button", name="Открыть").first.click()
    page.locator("#criteria").get_by_role("button", name="Уровни").first.click()
    editor = page.locator("#criteria textarea").first
    editor.fill("0: ноль\n3: три")
    page.locator("#criteria").get_by_role("button", name="Сохранить уровни").first.click()
    expect(page.locator("#criteria .levels-view").first).to_contain_text("3 — три", timeout=SLOW)

    page.locator("#criteria").get_by_role("button", name="Уровни").first.click()
    page.locator("#criteria .levels-editor textarea").first.fill("абв")
    page.locator("#criteria").get_by_role("button", name="Сохранить уровни").first.click()
    expect(page.locator("#c-err")).to_contain_text("должна быть вида", timeout=SLOW)
