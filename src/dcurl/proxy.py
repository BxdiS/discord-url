"""Ротация прокси для снижения блокировок по IP."""

from __future__ import annotations


class ProxyRotator:
    """Циклически перебирает список прокси на каждый запрос.

    Если прокси-серверов нет, всегда возвращает None — прямое соединение.
    """

    def __init__(self, urls: list[str] | None = None) -> None:
        self._urls = urls or []
        self._index = 0

    @property
    def enabled(self) -> bool:
        return bool(self._urls)

    def next(self) -> str | None:
        """Возвращает следующий URL или None."""
        if not self._urls:
            return None
        url = self._urls[self._index]
        self._index = (self._index + 1) % len(self._urls)
        return url
