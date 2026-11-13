from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    key: str
    title: str
    criteria: tuple[tuple[str, int], ...]


PRESETS: tuple[Preset, ...] = (
    Preset(
        "practice",
        "Практическая работа",
        (("Работа программы", 5), ("Качество кода", 3), ("Защита и ответы на вопросы", 2)),
    ),
    Preset(
        "coursework",
        "Курсовая работа",
        (
            ("Актуальность и постановка задачи", 10),
            ("Теоретическая часть", 20),
            ("Практическая часть", 40),
            ("Оформление", 10),
            ("Защита и ответы на вопросы", 20),
        ),
    ),
    Preset(
        "oral",
        "Устный ответ",
        (
            ("Полнота ответа", 4),
            ("Точность понятий", 3),
            ("Примеры и аргументы", 2),
            ("Связность речи", 1),
        ),
    ),
)

PRESET_KEYS = tuple(p.key for p in PRESETS)


def find_preset(key: str) -> Preset | None:
    return next((p for p in PRESETS if p.key == key), None)
