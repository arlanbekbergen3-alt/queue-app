import random
from collections.abc import Sequence

READY_BAND = 1_000_000
BAND_LIMIT = -READY_BAND // 2
TICKET_PARK = 10_000_000
ORDER_PARK = 1_500_000_000


def is_banded(sort_order: int) -> bool:
    return sort_order < BAND_LIMIT


def banded(sort_order: int) -> int:
    return sort_order if is_banded(sort_order) else sort_order - READY_BAND


def unbanded(sort_order: int) -> int:
    return sort_order + READY_BAND if is_banded(sort_order) else sort_order


def draw_order(ids: Sequence[str], rng: random.Random | None = None) -> list[str]:
    order = list(ids)
    (rng or random.SystemRandom()).shuffle(order)
    return order


def lottery_plan(
    groups: Sequence[Sequence[str]], rest: Sequence[str], rng: random.Random | None = None
) -> tuple[list[list[str]], dict[str, int]]:
    drawn = [draw_order(group, rng) for group in groups]
    numbers: dict[str, int] = {}
    ticket = 0
    for group in drawn:
        for entry_id in group:
            ticket += 1
            numbers[entry_id] = ticket
    for entry_id in rest:
        ticket += 1
        numbers[entry_id] = ticket
    return drawn, numbers
