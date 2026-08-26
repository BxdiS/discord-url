"""Отказоустойчивая ротация прокси с отслеживанием ошибок."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class ProxyHealth:
    """Здоровье одного прокси-адреса."""
    url: str
    failures: int = 0
    success_count: int = 0
    last_error: str | None = None
    last_error_time: datetime | None = None
    recovered_at: datetime | None = None

    @property
    def is_working(self) -> bool:
        """Прокси работает, если в последнее время было больше успехов чем ошибок."""
        return self.failures == 0

    def record_success(self) -> None:
        """Записать успешный запрос."""
        self.success_count += 1
        if self.failures > 0:
            self.failures = max(0, self.failures - 1)
            if self.is_working:
                self.recovered_at = datetime.now()
                log.info(f"прокси восстановилась: {self.url}")

    def record_failure(self, error: str) -> None:
        """Записать ошибку."""
        self.failures += 1
        self.last_error = error
        self.last_error_time = datetime.now()


class ProxyPool:
    """Управляет пулом прокси с отслеживанием здоровья и автоматическим переключением."""

    def __init__(
        self,
        urls: list[str] | None = None,
        max_failures: int = 3,
        failure_window_seconds: float = 300.0,
    ) -> None:
        """
        Args:
            urls: список URL прокси
            max_failures: макс ошибок перед временным отключением
            failure_window_seconds: окно времени для подсчёта ошибок
        """
        self._health = {url: ProxyHealth(url) for url in (urls or [])}
        self._index = 0
        self._max_failures = max_failures
        self._failure_window = timedelta(seconds=failure_window_seconds)
        self._current_url_index = 0

    @property
    def enabled(self) -> bool:
        """Есть ли вообще прокси."""
        return bool(self._health)

    @property
    def all_urls(self) -> list[str]:
        """Все URL в том же порядке."""
        return list(self._health.keys())

    def get_health(self, url: str) -> ProxyHealth | None:
        """Получить информацию о здоровье прокси."""
        return self._health.get(url)

    def next(self) -> str | None:
        """
        Возвращает следующий рабочий прокси или None.

        Логика:
        1. Циклически перебираем все прокси
        2. Пропускаем те, что в состоянии сбоя (failures > 0)
        3. Если рабочих нет, возвращаем первый доступный (с fallback)
        """
        if not self._health:
            return None

        urls = list(self._health.keys())
        attempts = 0

        while attempts < len(urls):
            url = urls[self._index]
            self._index = (self._index + 1) % len(urls)

            health = self._health[url]
            # Пропускаем явно сломанные прокси
            if health.is_working:
                return url

            attempts += 1

        # Все прокси сломаны, берём первую как fallback
        if urls:
            return urls[0]

        return None

    def record_success(self, url: str) -> None:
        """Отметить успешный запрос."""
        if url in self._health:
            health = self._health[url]
            health.record_success()
            if health.failures == 0:
                log.debug(f"прокси работает: {url}")

    def record_failure(self, url: str, error: str) -> None:
        """Отметить ошибку. После max_failures переключится на другую."""
        if url not in self._health:
            return

        health = self._health[url]
        health.record_failure(error)

        log.warning(f"ошибка прокси {url}: {error} (сбоев: {health.failures}/{self._max_failures})")

        if health.failures >= self._max_failures:
            log.error(f"прокси отключена из-за частых ошибок: {url}")

    def get_stats(self) -> dict[str, dict]:
        """Статистика по всем прокси."""
        stats = {}
        for url, health in self._health.items():
            stats[url] = {
                "working": health.is_working,
                "failures": health.failures,
                "success_count": health.success_count,
                "last_error": health.last_error,
                "last_error_time": health.last_error_time.isoformat() if health.last_error_time else None,
            }
        return stats
