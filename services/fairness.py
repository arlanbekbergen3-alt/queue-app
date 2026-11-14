import statistics
from collections import defaultdict
from collections.abc import Sequence

from pydantic import BaseModel

from core.config import settings

QUARTERS = 4
MIN_PER_FATIGUE_QUARTER = 2

DISCLAIMER = (
    "Это агрегаты без имён студентов. Они не доказывают ошибку или предвзятость: на баллы "
    "влияют вопросы, состав группы и порядок сдачи. Используйте их как повод посмотреть "
    "на свои оценки, а не как вывод."
)
ORDER_HINT = (
    "Средний результат по четвертям очереди (внутри каждой сессии). Если последняя четверть "
    "заметно ниже первой, возможно, к концу занятия оценки становятся строже или студенты "
    "устают. Это только гипотеза."
)
ATTEMPTS_HINT = (
    "Первые попытки и пересдачи считаются отдельно. Пересдачи обычно сильнее или слабее "
    "по естественным причинам, поэтому разница сама по себе ничего не доказывает."
)
SPREAD_HINT = (
    "Показывает, насколько оценки по одному критерию отличаются друг от друга. Если все оценки "
    "почти одинаковы или много крайних (0 и максимум), шкала используется грубо."
)
PEERS_HINT = (
    "Средние по одному и тому же критерию у других преподавателей, скопировавших эту работу из "
    "группы и согласившихся делиться сводками. Имён нет, порядок значений по возрастанию."
)


class QuarterStat(BaseModel):
    quarter: int
    count: int
    mean_percent: float | None


class OrderBlock(BaseModel):
    quarters: list[QuarterStat]
    drop_percent: float | None
    fatigue_flag: bool
    threshold_percent: float
    hint: str


class GroupMean(BaseModel):
    count: int
    mean_percent: float | None
    sufficient: bool


class AttemptsBlock(BaseModel):
    first: GroupMean
    retakes: GroupMean
    difference_percent: float | None
    hint: str


class SpreadRow(BaseModel):
    criterion_id: str
    name: str
    max_points: int
    scored: int
    mean_percent: float
    stdev_percent: float
    extreme_percent: float
    zero_percent: float
    top_percent: float


class SpreadBlock(BaseModel):
    rows: list[SpreadRow]
    extreme_percent: float | None
    hint: str


class PeerRow(BaseModel):
    name: str
    max_points: int
    own_percent: float
    peers: int
    peer_percents: list[float]


class PeersBlock(BaseModel):
    enabled: bool
    available: bool
    note: str
    rows: list[PeerRow]
    min_peers: int
    hint: str


class FairnessOut(BaseModel):
    submissions: int
    min_submissions: int
    sufficient: bool
    note: str
    disclaimer: str
    order: OrderBlock | None = None
    attempts: AttemptsBlock | None = None
    spread: SpreadBlock | None = None
    peers: PeersBlock


class Rated(BaseModel):
    session_id: str
    share: float
    attempt_number: int


def pct(part: float, whole: float) -> float:
    return round(100 * part / whole, 1)


def quarter_of(position: int, size: int) -> int:
    return min(QUARTERS - 1, position * QUARTERS // size)


def order_block(items: Sequence[Rated], drop_threshold: float) -> OrderBlock:
    sizes: dict[str, int] = defaultdict(int)
    for item in items:
        sizes[item.session_id] += 1
    seen: dict[str, int] = defaultdict(int)
    buckets: list[list[float]] = [[] for _ in range(QUARTERS)]
    for item in items:
        position = seen[item.session_id]
        seen[item.session_id] += 1
        buckets[quarter_of(position, sizes[item.session_id])].append(item.share)
    stats = [
        QuarterStat(
            quarter=index + 1,
            count=len(values),
            mean_percent=pct(statistics.mean(values), 1) if values else None,
        )
        for index, values in enumerate(buckets)
    ]
    first, last = stats[0], stats[-1]
    drop = None
    if (
        first.count >= MIN_PER_FATIGUE_QUARTER
        and last.count >= MIN_PER_FATIGUE_QUARTER
        and first.mean_percent is not None
        and last.mean_percent is not None
    ):
        drop = round(first.mean_percent - last.mean_percent, 1)
    return OrderBlock(
        quarters=stats,
        drop_percent=drop,
        fatigue_flag=drop is not None and drop >= drop_threshold,
        threshold_percent=drop_threshold,
        hint=ORDER_HINT,
    )


def group_mean(shares: list[float], minimum: int) -> GroupMean:
    if len(shares) < minimum:
        return GroupMean(count=len(shares), mean_percent=None, sufficient=False)
    return GroupMean(
        count=len(shares), mean_percent=pct(statistics.mean(shares), 1), sufficient=True
    )


def attempts_block(items: Sequence[Rated], minimum: int) -> AttemptsBlock:
    first = group_mean([i.share for i in items if i.attempt_number <= 1], minimum)
    retakes = group_mean([i.share for i in items if i.attempt_number > 1], minimum)
    difference = None
    if first.mean_percent is not None and retakes.mean_percent is not None:
        difference = round(retakes.mean_percent - first.mean_percent, 1)
    return AttemptsBlock(
        first=first, retakes=retakes, difference_percent=difference, hint=ATTEMPTS_HINT
    )


def spread_block(
    scores: dict[str, list[int]], criteria: dict[str, tuple[str, int]], minimum: int
) -> SpreadBlock:
    rows: list[SpreadRow] = []
    extreme_total = 0
    scored_total = 0
    for criterion_id, (name, maximum) in criteria.items():
        points = scores.get(criterion_id, [])
        if len(points) < minimum or maximum <= 0:
            continue
        zero = sum(1 for p in points if p == 0)
        top = sum(1 for p in points if p == maximum)
        extreme_total += zero + top
        scored_total += len(points)
        rows.append(
            SpreadRow(
                criterion_id=criterion_id,
                name=name,
                max_points=maximum,
                scored=len(points),
                mean_percent=pct(statistics.mean(points), maximum),
                stdev_percent=pct(statistics.stdev(points), maximum),
                extreme_percent=pct(zero + top, len(points)),
                zero_percent=pct(zero, len(points)),
                top_percent=pct(top, len(points)),
            )
        )
    return SpreadBlock(
        rows=rows,
        extreme_percent=pct(extreme_total, scored_total) if scored_total else None,
        hint=SPREAD_HINT,
    )


def peers_block(
    *,
    enabled: bool,
    has_origin: bool,
    own: dict[tuple[str, int], tuple[float, int]],
    others: Sequence[tuple[str, str, int, float, int]],
    minimum: int,
) -> PeersBlock:
    needed = settings.FAIRNESS_MIN_PEERS
    if not enabled:
        return PeersBlock(
            enabled=False,
            available=False,
            note="Выключено. Включите согласие в настройках работы, чтобы делиться сводками "
            "и видеть сводки других.",
            rows=[],
            min_peers=needed,
            hint=PEERS_HINT,
        )
    if not has_origin:
        return PeersBlock(
            enabled=True,
            available=False,
            note="Сравнение доступно только для работ, скопированных из предметной группы.",
            rows=[],
            min_peers=needed,
            hint=PEERS_HINT,
        )
    pooled: dict[tuple[str, int], list[float]] = defaultdict(list)
    for _, name, maximum, mean, count in others:
        if count >= minimum and maximum > 0:
            pooled[(name, maximum)].append(round(100 * mean / maximum, 1))
    rows = []
    for (name, maximum), (own_mean, own_count) in own.items():
        values = sorted(pooled.get((name, maximum), []))
        if own_count < minimum or len(values) < needed or maximum <= 0:
            continue
        rows.append(
            PeerRow(
                name=name,
                max_points=maximum,
                own_percent=round(100 * own_mean / maximum, 1),
                peers=len(values),
                peer_percents=values,
            )
        )
    note = ""
    if not rows:
        note = (
            f"Недостаточно данных (нужно не менее {minimum} сдач у каждого участника и не менее "
            f"{needed} других участников, согласившихся делиться)."
        )
    return PeersBlock(
        enabled=True, available=True, note=note, rows=rows, min_peers=needed, hint=PEERS_HINT
    )


def build_fairness(
    *,
    items: Sequence[Rated],
    criterion_scores: dict[str, list[int]],
    criteria: dict[str, tuple[str, int]],
    minimum: int,
    peers: PeersBlock,
) -> FairnessOut:
    count = len(items)
    if count < minimum:
        return FairnessOut(
            submissions=count,
            min_submissions=minimum,
            sufficient=False,
            note=f"Недостаточно данных (нужно не менее {minimum} сдач, сейчас {count}).",
            disclaimer=DISCLAIMER,
            peers=peers,
        )
    return FairnessOut(
        submissions=count,
        min_submissions=minimum,
        sufficient=True,
        note="",
        disclaimer=DISCLAIMER,
        order=order_block(items, settings.FAIRNESS_FATIGUE_DROP_PERCENT),
        attempts=attempts_block(items, minimum),
        spread=spread_block(criterion_scores, criteria, minimum),
        peers=peers,
    )
