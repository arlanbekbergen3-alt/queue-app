import statistics
from dataclasses import dataclass, field

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import NotFoundError
from repositories.protocol import ProtocolRepository
from repositories.queue import QueueRepository
from services.queue import normalize_code

BANDS = (("low", "низкий", 0.0), ("mid", "средний", 0.5), ("high", "высокий", 0.8))


@dataclass
class SubmissionData:
    ticket: int
    duration_seconds: int
    total_points: int
    max_points: int
    scores: dict[str, int] = field(default_factory=dict)


class DurationStats(BaseModel):
    median: float
    p25: float
    p75: float
    min: int
    max: int


class CriterionStats(BaseModel):
    criterion_id: str
    name: str
    max_points: int
    mean: float
    distribution: dict[str, int]


class BandStats(BaseModel):
    band: str
    label: str
    count: int
    median_duration: float | None


class Flag(BaseModel):
    ticket: int
    ratio: float
    band: str
    text: str


class AnalyticsOut(BaseModel):
    submissions: int
    min_submissions: int
    sufficient: bool
    note: str
    duration: DurationStats | None = None
    criteria: list[CriterionStats] = []
    bands: list[BandStats] = []
    flags: list[Flag] = []


def band_of(total: int, maximum: int) -> str:
    share = total / maximum if maximum else 0.0
    name = BANDS[0][0]
    for key, _, lower in BANDS:
        if share >= lower:
            name = key
    return name


def quantile(values: list[int], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def ratio_text(ticket: int, ratio: float, label: str) -> str:
    number = f"{ratio:.1f}".replace(".", ",")
    return (
        f"Сдача №{ticket} длилась {number} раза дольше медианы при близком результате "
        f"(группа «{label}»). Стоит посмотреть, что заняло время."
    )


def analyze(
    items: list[SubmissionData],
    criteria: dict[str, tuple[str, int]],
    *,
    min_submissions: int | None = None,
    outlier_factor: float | None = None,
) -> AnalyticsOut:
    minimum = min_submissions if min_submissions is not None else settings.ANALYTICS_MIN_SUBMISSIONS
    factor = outlier_factor if outlier_factor is not None else settings.ANALYTICS_OUTLIER_FACTOR
    count = len(items)
    if count < minimum:
        return AnalyticsOut(
            submissions=count,
            min_submissions=minimum,
            sufficient=False,
            note=f"Мало данных: завершённых сдач {count}, для выводов нужно не меньше {minimum}.",
        )
    durations = [i.duration_seconds for i in items]
    stats = DurationStats(
        median=float(statistics.median(durations)),
        p25=quantile(durations, 0.25),
        p75=quantile(durations, 0.75),
        min=min(durations),
        max=max(durations),
    )
    criteria_stats = []
    for criterion_id, (name, maximum) in criteria.items():
        points = [i.scores[criterion_id] for i in items if criterion_id in i.scores]
        if not points:
            continue
        distribution = {str(p): points.count(p) for p in range(maximum + 1)}
        criteria_stats.append(
            CriterionStats(
                criterion_id=criterion_id,
                name=name,
                max_points=maximum,
                mean=round(statistics.mean(points), 2),
                distribution=distribution,
            )
        )
    grouped: dict[str, list[SubmissionData]] = {key: [] for key, _, _ in BANDS}
    for item in items:
        grouped[band_of(item.total_points, item.max_points)].append(item)
    bands = []
    flags = []
    for key, label, _ in BANDS:
        members = grouped[key]
        enough = len(members) >= minimum
        median = float(statistics.median(m.duration_seconds for m in members)) if enough else None
        bands.append(BandStats(band=key, label=label, count=len(members), median_duration=median))
        if median:
            for member in members:
                ratio = member.duration_seconds / median
                if ratio > factor:
                    flags.append(
                        Flag(
                            ticket=member.ticket,
                            ratio=round(ratio, 2),
                            band=key,
                            text=ratio_text(member.ticket, ratio, label),
                        )
                    )
    flags.sort(key=lambda f: -f.ratio)
    return AnalyticsOut(
        submissions=count,
        min_submissions=minimum,
        sufficient=True,
        note=(
            "Это агрегаты для самоанализа, а не вывод о конкретном студенте: "
            "длительность зависит от вопросов, пересказов и пауз."
        ),
        duration=stats,
        criteria=criteria_stats,
        bands=bands,
        flags=flags,
    )


class AnalyticsService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ProtocolRepository(db)
        self.queue_repo = QueueRepository(db)

    async def _load(
        self, assignment_id: str, session_id: str | None
    ) -> tuple[list[SubmissionData], dict[str, tuple[str, int]]]:
        criteria = {
            c.id: (c.name, c.max_points) for c in await self.repo.list_criteria(assignment_id)
        }
        rows = await self.repo.completed_for_analytics(assignment_id, session_id)
        items = [
            SubmissionData(
                ticket=ticket,
                duration_seconds=sub.duration_seconds or 0,
                total_points=sub.total_points or 0,
                max_points=sub.max_points or 0,
                scores=scores,
            )
            for sub, ticket, scores in rows
        ]
        return items, criteria

    async def for_assignment(self, teacher_id: str, assignment_id: str) -> AnalyticsOut:
        if await self.repo.get_assignment(assignment_id, teacher_id) is None:
            raise NotFoundError("Assignment not found", code="assignment_not_found")
        items, criteria = await self._load(assignment_id, None)
        return analyze(items, criteria)

    async def for_session(self, code: str, teacher_id: str) -> AnalyticsOut:
        session = await self.queue_repo.get_session(normalize_code(code))
        if session is None or session.teacher_id != teacher_id:
            raise NotFoundError("Session not found", code="session_not_found")
        if session.assignment_id is None:
            raise NotFoundError("The session has no assignment", code="submission_not_found")
        items, criteria = await self._load(session.assignment_id, session.id)
        return analyze(items, criteria)
