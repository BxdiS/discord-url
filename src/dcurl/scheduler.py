"""Цикл опроса: по задаче на код, общий лимитер, подтверждение перед алертом."""

from __future__ import annotations

import asyncio
import logging
import random

from .api import CheckResult, DiscordClient, Status
from .config import PollingConfig
from .messages import build_transition_message, describe_taken
from .state import Store, Transition, utcnow

log = logging.getLogger("dcurl.watch")

# Проверять чуть позже заявленного истечения, а не ровно в него: у Discord
# срок снимается не мгновенно, и преждевременный запрос ничего не даст.
EXPIRY_MARGIN_SECONDS = 3.0


class Watcher:
    def __init__(
        self,
        *,
        codes: list[str],
        client: DiscordClient,
        store: Store,
        polling: PollingConfig,
        notifier=None,
        skip_free_hours: float = 0,
    ) -> None:
        self._codes = codes
        self._client = client
        self._store = store
        self._polling = polling
        self._notifier = notifier
        self._skip_free_hours = skip_free_hours

    async def run(self) -> None:
        """Крутит опрос до отмены. Состояние сохраняется при выходе."""
        self._store.load()
        self._store.forget(set(self._codes))

        tasks = [
            asyncio.create_task(self._watch(code), name=f"watch:{code}")
            for code in self._codes
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            self._store.save()

    async def _watch(self, code: str) -> None:
        while True:
            result = None
            try:
                if self._should_skip(code):
                    log.debug("[%s] пропуск: свежий свободный код", code)
                    result = None
                else:
                    result = await self.tick(code)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Падение по одному коду не должно уносить остальные.
                log.exception("[%s] непредвиденная ошибка в цикле проверки", code)
            await asyncio.sleep(self._next_delay(result))

    async def tick(self, code: str) -> CheckResult:
        """Одна проверка кода: запрос, подтверждение, запись, уведомление."""
        result = await self._client.check(code)

        # Подтверждаем только новое освобождение. Если код уже числится
        # свободным, тратить на него лишние запросы каждый цикл незачем.
        if result.status is Status.FREE and self._needs_confirmation(code):
            result = await self._confirm(code, result)

        transition = self._store.record(result)
        self._log_result(result, transition)

        if transition is None:
            return result

        self._store.append_history(transition)
        self._store.save()
        await self._announce(transition)
        return result

    def _should_skip(self, code: str) -> bool:
        """Пропускаем проверку свежих свободных кодов, чтобы сэкономить трафик."""
        if self._skip_free_hours <= 0:
            return False
        known = self._store.get(code)
        if known is None or known.status is not Status.FREE:
            return False
        # Код свободен и был проверен недавно
        elapsed = (utcnow() - known.since).total_seconds() / 3600
        return elapsed < self._skip_free_hours

    def _needs_confirmation(self, code: str) -> bool:
        known = self._store.get(code)
        return known is None or known.status is not Status.FREE

    async def _confirm(self, code: str, first: CheckResult) -> CheckResult:
        """Перепроверяет освободившийся код несколько раз подряд.

        Одиночный 404 может быть следствием кратковременного сбоя Discord.
        Алерт уходит, только если все подтверждения тоже дали FREE; любой
        другой исход возвращается как есть и алерта не вызывает.
        """
        needed = self._polling.confirmations - 1
        if needed <= 0:
            return first

        for attempt in range(1, needed + 1):
            await asyncio.sleep(self._polling.confirm_delay_seconds)
            again = await self._client.check(code)
            if again.status is not Status.FREE:
                log.info(
                    "[%s] 404 не подтвердился на попытке %d/%d (получено %s) — алерта не будет",
                    code,
                    attempt,
                    needed,
                    again.status.value,
                )
                return again

        log.info("[%s] освобождение подтверждено %d проверками", code, needed + 1)
        return first

    async def _announce(self, transition: Transition) -> None:
        text = build_transition_message(transition)
        if text is None or self._notifier is None:
            return
        try:
            await self._notifier.send(text)
        except Exception:
            # Уведомление — не повод падать: статус уже записан в историю.
            log.exception("[%s] не удалось отправить уведомление", transition.code)

    def _next_delay(self, result: CheckResult | None) -> float:
        base = self._jittered(self._polling.interval_seconds)

        # Discord сам сообщил, когда приглашение истечёт: нет смысла долбить
        # код всё это время, достаточно проснуться сразу после срока.
        if result is not None and result.status is Status.TAKEN and result.expires_at:
            remaining = (result.expires_at - utcnow()).total_seconds()
            if remaining <= 0:
                return min(base, 5.0)
            return max(1.0, min(base, remaining + EXPIRY_MARGIN_SECONDS))

        return base

    def _jittered(self, seconds: float) -> float:
        jitter = self._polling.jitter
        factor = 1.0 + random.uniform(-jitter, jitter) if jitter else 1.0
        return max(1.0, seconds * factor)

    def _log_result(self, result: CheckResult, transition: Transition | None) -> None:
        if result.status is Status.UNKNOWN:
            log.warning("[%s] статус неизвестен: %s", result.code, result.detail)
            return

        if transition is None:
            log.debug("[%s] без изменений: %s", result.code, result.status.value)
            return

        if result.status is Status.FREE:
            log.warning("[%s] СВОБОДЕН", result.code)
        else:
            log.info("[%s] занят: %s", result.code, describe_taken(result))
