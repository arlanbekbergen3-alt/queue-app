import re
from dataclasses import dataclass

MAX_QUESTION_LENGTH = 1000

_MARKER = re.compile(
    r"^\s*(?:"
    r"\(?\d{1,4}[.)\]:]"
    r"|\(\d{1,4}\)"
    r"|\d{1,4}\s*[-–—]"
    r"|[-–—•·*▪►>+]"
    r"|[a-zA-Zа-яА-Я][.)]"
    r")(?:\s+|$)"
)


@dataclass(frozen=True)
class ParsedLine:
    text: str
    status: str


def clean_line(raw: str) -> str:
    line = raw.replace(" ", " ").strip()
    previous = None
    while previous != line:
        previous = line
        line = _MARKER.sub("", line, count=1).strip()
    return " ".join(line.split())


def parse(
    raw: str, existing: set[str], remaining_capacity: int, max_length: int = MAX_QUESTION_LENGTH
) -> list[ParsedLine]:
    seen = {item.casefold() for item in existing}
    result: list[ParsedLine] = []
    accepted = 0
    for raw_line in raw.splitlines():
        text = clean_line(raw_line)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            result.append(ParsedLine(text, "duplicate"))
            continue
        seen.add(key)
        if len(text) > max_length:
            result.append(ParsedLine(text, "too_long"))
        elif accepted >= remaining_capacity:
            result.append(ParsedLine(text, "over_limit"))
        else:
            accepted += 1
            result.append(ParsedLine(text, "ok"))
    return result
