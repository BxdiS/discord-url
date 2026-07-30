"""Общий ограничитель запросов: token bucket плюс глобальная пауза на 429."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Пропускает не более `rate_per_second` запросов в секунду на весь процесс.

    Помимо равномерного темпа умеет глобальную паузу: получив 429, любой
    воркер вызывает `pause()`, и все остальные тоже останавливаются. Именно
    накопление 429 (а не 404) приводит к временной блокировке IP на стороне
    Cloudflare, поэтому тормозить надо всем сразу, а не только «виноватому».
    """

    def __init__(self, rate_per_second: float, burst: float | None = None) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second должен быть больше 0")
        self._rate = rate_per_second
        self._capacity = burst if burst is not None else max(1.0, rate_per_second)
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._resume_at = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Ждёт до момента, когда запрос разрешён, и списывает один токен."""
        while True:
            async with self._lock:
                now = time.monotonic()
                wait = self._resume_at - now
                if wait <= 0:
                    elapsed = now - self._updated
                    self._tokens = min(
                        self._capacity, self._tokens + elapsed * self._rate
                    )
                    self._updated = now
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return
                    wait = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait)

    def pause(self, seconds: float) -> None:
        """Останавливает все запросы минимум на `seconds`."""
        if seconds <= 0:
            return
        self._resume_at = max(self._resume_at, time.monotonic() + seconds)

    @property
    def paused_for(self) -> float:
        """Сколько секунд осталось до снятия глобальной паузы."""
        return max(0.0, self._resume_at - time.monotonic())
