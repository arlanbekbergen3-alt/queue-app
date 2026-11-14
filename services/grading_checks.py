import statistics
from dataclasses import dataclass

from core.config import settings


@dataclass(frozen=True)
class Deviation:
    samples: int
    mean: float
    distance: float | None
    warn: bool


def is_extreme(points: int, maximum: int) -> bool:
    return points == 0 or points == maximum


def reason_given(comment: str) -> bool:
    return len(comment.strip()) >= settings.EXTREME_REASON_MIN_LENGTH


def check_deviation(points: int, history: list[int], sigma: float) -> Deviation | None:
    if len(history) < settings.DEVIATION_MIN_SAMPLES:
        return None
    mean = statistics.mean(history)
    spread = statistics.stdev(history)
    if spread == 0:
        return Deviation(len(history), mean, None, points != mean)
    distance = abs(points - mean) / spread
    return Deviation(len(history), mean, distance, distance > sigma)


def format_number(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def deviation_text(mean: float, points: int) -> str:
    return (
        f"Ваши оценки по этому критерию обычно около {format_number(mean)}, "
        f"сейчас {points}. Проверьте."
    )
