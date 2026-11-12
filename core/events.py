from collections.abc import Callable

_listeners: list[Callable[[str], None]] = []


def add_listener(listener: Callable[[str], None]) -> None:
    if listener not in _listeners:
        _listeners.append(listener)


def notify_changed(code: str) -> None:
    for listener in _listeners:
        listener(code)
