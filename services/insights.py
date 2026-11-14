import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from core.config import settings

MAX_INSIGHTS = 5
MIN_INSIGHTS = 3
GOOD_MEAN_PERCENT = 80.0
SLOW_FACTOR = 1.3
FAST_FACTOR = 0.6

SEVERITY_ORDER = {"warn": 0, "info": 1}
NOTE = "Выводы считаются по фиксированным правилам из ваших данных и не заменяют вашего суждения."


class Insight(BaseModel):
    key: str
    severity: str
    text: str
    action: str


class InsightsOut(BaseModel):
    sufficient: bool
    min_submissions: int
    note: str
    items: list[Insight] = []


@dataclass
class InsightInput:
    total_percent: float | None
    duration_seconds: int


@dataclass
class CriterionInput:
    name: str
    max_points: int
    points: Sequence[int]


def plural_ru(count: int, one: str, few: str, many: str) -> str:
    n = abs(count)
    last, tail = n % 10, n % 100
    if last == 1 and tail != 11:
        return one
    if 2 <= last <= 4 and not 12 <= tail <= 14:
        return few
    return many


def number(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text.replace(".", ",")


def percent(part: float, whole: float) -> float:
    return round(100 * part / whole, 1)


def clock(seconds: float) -> str:
    total = max(0, round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def note_for(count: int, minimum: int) -> str:
    return f"Недостаточно данных (нужно не менее {minimum} сдач, сейчас {count})."


def quoted(names: list[str]) -> str:
    return ", ".join(f"«{n}»" for n in names)


def weakest(criteria: list[CriterionInput], minimum: int) -> Insight | None:
    scored = [
        (percent(statistics.mean(c.points), c.max_points), c)
        for c in criteria
        if c.max_points > 0 and len(c.points) >= minimum
    ]
    if not scored:
        return None
    mean_percent, criterion = min(scored, key=lambda pair: (pair[0], pair[1].name))
    below = percent(
        sum(1 for p in criterion.points if p * 2 < criterion.max_points), len(criterion.points)
    )
    if mean_percent >= GOOD_MEAN_PERCENT:
        return Insight(
            key="weakest",
            severity="info",
            text=(
                f"Даже самый слабый критерий «{criterion.name}» выполнен уверенно: в среднем "
                f"{number(mean_percent)}% от максимума."
            ),
            action="Можно усложнить задание или добавить более тонкие уровни оценки.",
        )
    return Insight(
        key="weakest",
        severity="warn",
        text=(
            f"Самый слабый критерий «{criterion.name}»: в среднем {number(mean_percent)}% "
            f"от максимума, у {number(below)}% сдач меньше половины баллов."
        ),
        action="Разберите типичные ошибки по этому критерию на следующем занятии.",
    )


def flat(criteria: list[CriterionInput], minimum: int) -> Insight | None:
    names = [c.name for c in criteria if len(c.points) >= minimum and len(set(c.points)) == 1]
    if not names:
        return None
    value = next(c for c in criteria if c.name == names[0]).points[0]
    if len(names) == 1:
        text = (
            f"Критерий «{names[0]}» не различает студентов: все получили одинаково "
            f"({value} {plural_ru(value, 'балл', 'балла', 'баллов')})."
        )
    else:
        text = f"Критерии {quoted(names)} не различают студентов: у всех одинаковые оценки."
    return Insight(
        key="flat",
        severity="warn",
        text=text,
        action="Проверьте описания уровней: возможно, критерий слишком простой или слишком общий.",
    )


def zeros(criteria: list[CriterionInput], minimum: int) -> Insight | None:
    limit = settings.INSIGHT_ZEROS_PERCENT
    shares = [
        (percent(sum(1 for p in c.points if p == 0), len(c.points)), c)
        for c in criteria
        if len(c.points) >= minimum
    ]
    shares = [pair for pair in shares if pair[0] >= limit]
    if not shares:
        return None
    share, criterion = max(shares, key=lambda pair: (pair[0], pair[1].name))
    return Insight(
        key="zeros",
        severity="warn",
        text=f"По критерию «{criterion.name}» нулевая оценка у {number(share)}% сдач.",
        action="Уточните, что именно требовалось: возможно, часть группы не поняла задание.",
    )


def extremes(criteria: list[CriterionInput], minimum: int) -> Insight | None:
    totals = [(len(c.points), sum(1 for p in c.points if p in (0, c.max_points))) for c in criteria]
    count = sum(t for t, _ in totals)
    if not count or count < minimum:
        return None
    share = percent(sum(e for _, e in totals), count)
    if share < settings.INSIGHT_EXTREME_PERCENT:
        return None
    return Insight(
        key="extremes",
        severity="warn",
        text=(
            f"Крайние оценки (0 или максимум) составляют {number(share)}% всех оценок: "
            "промежуточные баллы почти не используются."
        ),
        action="Опишите уровни между нулём и максимумом, чтобы оценки отражали разницу.",
    )


def pace(items: Sequence[InsightInput]) -> Insight | None:
    durations = [i.duration_seconds for i in items if i.duration_seconds > 0]
    if not durations:
        return None
    median = float(statistics.median(durations))
    plan = float(settings.DEFAULT_SERVICE_SECONDS)
    ratio = median / plan
    if ratio >= SLOW_FACTOR:
        return Insight(
            key="pace",
            severity="warn",
            text=(
                f"Типичная сдача длится {clock(median)} при плане около {clock(plan)}: "
                f"в {number(ratio)} раза дольше."
            ),
            action="Если очередь длинная, уменьшите число вопросов на сдачу в настройках работы.",
        )
    if ratio <= FAST_FACTOR:
        return Insight(
            key="pace",
            severity="info",
            text=(
                f"Типичная сдача длится {clock(median)} при плане около {clock(plan)}: "
                "заметно быстрее."
            ),
            action="Можно добавить вопросов на сдачу или сократить время на занятие.",
        )
    return Insight(
        key="pace",
        severity="info",
        text=f"Типичная сдача длится {clock(median)} при плане около {clock(plan)}: укладываетесь.",
        action="Менять число вопросов на сдачу не нужно.",
    )


def overall(items: Sequence[InsightInput]) -> Insight | None:
    shares = [i.total_percent for i in items if i.total_percent is not None]
    if not shares:
        return None
    mean = statistics.mean(shares)
    below = percent(sum(1 for s in shares if s < 50), len(shares))
    return Insight(
        key="overall",
        severity="warn" if below >= 40 else "info",
        text=(
            f"Средний результат: {number(mean)}% от максимума, у {number(below)}% сдач "
            "меньше половины баллов."
        ),
        action=(
            "Многие сдают ниже половины: стоит пересмотреть сложность или подготовку."
            if below >= 40
            else "Результат в норме, смотрите отдельные критерии ниже."
        ),
    )


def fatigue(drop_percent: float | None, flagged: bool) -> Insight | None:
    if not flagged or drop_percent is None:
        return None
    return Insight(
        key="fatigue",
        severity="warn",
        text=(f"К концу очереди результаты ниже на {number(drop_percent)} п. п., чем в начале."),
        action=(
            "Это не доказательство ошибки: возможно, студенты устают или сильные сдавали первыми. "
            "Посмотрите детали в разделе «Подробнее»."
        ),
    )


def build_insights(
    items: Sequence[InsightInput],
    criteria: list[CriterionInput],
    *,
    minimum: int,
    fatigue_drop: float | None = None,
    fatigue_flag: bool = False,
) -> InsightsOut:
    if len(items) < minimum:
        return InsightsOut(
            sufficient=False, min_submissions=minimum, note=note_for(len(items), minimum)
        )
    rules = [
        weakest(criteria, minimum),
        flat(criteria, minimum),
        zeros(criteria, minimum),
        extremes(criteria, minimum),
        fatigue(fatigue_drop, fatigue_flag),
        pace(items),
    ]
    found = [r for r in rules if r is not None]
    found.sort(key=lambda r: SEVERITY_ORDER[r.severity])
    summary = overall(items)
    if summary is not None and len(found) < MIN_INSIGHTS:
        found.append(summary)
    return InsightsOut(
        sufficient=True,
        min_submissions=minimum,
        note=NOTE,
        items=found[:MAX_INSIGHTS],
    )
