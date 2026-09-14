"""Отправка уведомлений через Telegram Bot API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("dcurl.telegram")

API_BASE = "https://api.telegram.org"
DEFAULT_RETRY_AFTER = 3.0


class TelegramError(Exception):
    """Telegram отклонил запрос по причине, которую повтор не исправит."""


class TelegramNotifier:
    """Шлёт сообщения в один чат.

    Токен нигде не логируется: он входит в путь запроса, поэтому текст любой
    ошибки перед записью в лог проходит через редактирование.
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        proxy: str | None = None,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._max_retries = max_retries
        self._http = httpx.AsyncClient(
            base_url=f"{API_BASE}/bot{token}",
            timeout=timeout,
            proxy=proxy,
        )

    async def __aenter__(self) -> TelegramNotifier:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    async def _call(self, method: str, payload: dict[str, Any]) -> dict:
        last_error = "неизвестная ошибка"

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http.post(f"/{method}", json=payload)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {self._redact(str(exc))}"
                await asyncio.sleep(2**attempt)
                continue

            try:
                body = response.json()
            except ValueError:
                body = {}

            if response.status_code == 200 and body.get("ok"):
                return body.get("result") or {}

            description = str(body.get("description") or f"HTTP {response.status_code}")

            if response.status_code == 429:
                retry_after = (body.get("parameters") or {}).get("retry_after")
                delay = float(retry_after) if retry_after else DEFAULT_RETRY_AFTER
                log.warning("Telegram лимит, ждём %.1f с", delay)
                await asyncio.sleep(delay)
                last_error = description
                continue

            if 400 <= response.status_code < 500:
                # Неверный токен, неизвестный chat_id, бот заблокирован —
                # повторять бессмысленно.
                raise TelegramError(self._redact(description))

            last_error = description
            await asyncio.sleep(2**attempt)

        raise TelegramError(self._redact(last_error))

    async def send(self, text: str) -> None:
        await self._call(
            "sendMessage",
            {
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )

    async def whoami(self) -> str:
        """Возвращает @username бота — быстрая проверка, что токен рабочий."""
        result = await self._call("getMe", {})
        return result.get("username") or "?"
