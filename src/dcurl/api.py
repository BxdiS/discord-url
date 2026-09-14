"""Клиент публичного эндпоинта Discord `GET /invites/{code}`.

Авторизация не нужна: эндпоинт открытый. Соответствие ответов и статусов:

    200 -> код занят, в теле объект приглашения с данными сервера
    404 -> код свободен (`{"message": "Unknown Invite", "code": 10006}`)
    429 -> превышен лимит, не статус: ставим глобальную паузу и повторяем
    прочее -> UNKNOWN, никаких выводов о доступности не делаем
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import httpx

from . import USER_AGENT
from .limiter import RateLimiter

DISCORD_API_BASE = "https://discord.com/api/v10"


def api_base() -> str:
    """Базовый URL API. DCURL_API_BASE подменяет его для локальных прогонов."""
    return os.environ.get("DCURL_API_BASE") or DISCORD_API_BASE

# Код ошибки Discord для несуществующего приглашения.
UNKNOWN_INVITE = 10006

# Если Discord не сказал, сколько ждать после 429.
DEFAULT_RETRY_AFTER = 5.0
# Защита от мусорного значения в ответе, но реальные паузы уважаем.
MAX_RETRY_AFTER = 3600.0


class Status(str, Enum):
    TAKEN = "taken"
    FREE = "free"
    UNKNOWN = "unknown"


# Значения поля `type` в объекте приглашения.
INVITE_TYPE_GUILD = 0
INVITE_TYPE_GROUP_DM = 1
INVITE_TYPE_FRIEND = 2

INVITE_TYPE_LABEL = {
    INVITE_TYPE_GUILD: "сервер",
    INVITE_TYPE_GROUP_DM: "групповой чат",
    INVITE_TYPE_FRIEND: "дружеская ссылка",
}


@dataclass(frozen=True)
class CheckResult:
    code: str
    status: Status
    guild_name: str | None = None
    guild_id: str | None = None
    member_count: int | None = None
    is_vanity: bool = False
    # Момент, когда приглашение истечёт само. Для vanity — None (бессрочно).
    # Если он известен, код освободится именно тогда, и гадать не нужно.
    expires_at: datetime | None = None
    invite_type: int | None = None
    detail: str | None = None

    @property
    def is_conclusive(self) -> bool:
        """UNKNOWN не даёт права ни поднимать алерт, ни писать переход в историю."""
        return self.status is not Status.UNKNOWN

    @property
    def type_label(self) -> str:
        return INVITE_TYPE_LABEL.get(self.invite_type, "неизвестный тип")


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def parse_timestamp(value: Any) -> datetime | None:
    """Разбирает ISO-время Discord вида 2026-07-30T17:51:45.690172+00:00."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def retry_after_seconds(response: httpx.Response) -> float:
    """Сколько ждать после 429.

    Значение из тела точнее заголовка: Discord отдаёт там дробные секунды.
    """
    value: Any = None
    body = _json_or_none(response)
    if isinstance(body, dict):
        value = body.get("retry_after")
    if value is None:
        value = response.headers.get("retry-after")

    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        seconds = DEFAULT_RETRY_AFTER

    if seconds != seconds:  # NaN
        seconds = DEFAULT_RETRY_AFTER
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


def build_client(
    timeout: float = 10.0, base_url: str | None = None, proxy: str | None = None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url or api_base(),
        timeout=timeout,
        proxy=proxy,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=False,
    )


class DiscordClient:
    """Проверяет коды, соблюдая общий лимит и паузы после 429."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        limiter: RateLimiter,
        *,
        max_retries: int = 5,
        on_rate_limit=None,
    ) -> None:
        self._http = http
        self._limiter = limiter
        self._max_retries = max_retries
        self._on_rate_limit = on_rate_limit

    async def check(self, code: str) -> CheckResult:
        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()

            try:
                response = await self._http.get(
                    f"/invites/{code}", params={"with_counts": "true"}
                )
            except httpx.HTTPError as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                if attempt < self._max_retries:
                    continue
                return CheckResult(
                    code=code,
                    status=Status.UNKNOWN,
                    detail=f"сетевая ошибка: {error_msg}",
                )

            if response.status_code == 429:
                delay = retry_after_seconds(response)
                self._limiter.pause(delay)
                if self._on_rate_limit is not None:
                    scope = response.headers.get("x-ratelimit-scope", "?")
                    self._on_rate_limit(code, delay, scope)
                continue

            return self._interpret(code, response)

        return CheckResult(
            code=code,
            status=Status.UNKNOWN,
            detail=f"не удалось получить ответ за {self._max_retries + 1} попыток (429)",
        )

    def _interpret(self, code: str, response: httpx.Response) -> CheckResult:
        if response.status_code == 200:
            return self._parse_invite(code, response)

        if response.status_code == 404:
            body = _json_or_none(response)
            if isinstance(body, dict) and body.get("code") == UNKNOWN_INVITE:
                return CheckResult(code=code, status=Status.FREE)
            # 404 не от Discord (например, страница ошибки Cloudflare во время
            # сбоя) — свободным считать нельзя, иначе будет ложный алерт.
            return CheckResult(
                code=code,
                status=Status.UNKNOWN,
                detail="404 без тела Discord — похоже на ответ прокси, а не API",
            )

        return CheckResult(
            code=code,
            status=Status.UNKNOWN,
            detail=f"неожиданный HTTP {response.status_code}",
        )

    def _parse_invite(self, code: str, response: httpx.Response) -> CheckResult:
        body = _json_or_none(response)
        if not isinstance(body, dict):
            return CheckResult(
                code=code,
                status=Status.UNKNOWN,
                detail="HTTP 200 с нечитаемым телом",
            )

        # `guild` бывает null: код может занимать не сервер, а дружеская
        # ссылка или групповой чат — у них поля сервера просто нет.
        guild = body.get("guild") or {}
        member_count = body.get("approximate_member_count")
        expires_at = parse_timestamp(body.get("expires_at"))
        invite_type = body.get("type")
        if not isinstance(invite_type, int):
            invite_type = None

        return CheckResult(
            code=code,
            status=Status.TAKEN,
            guild_name=guild.get("name"),
            guild_id=guild.get("id"),
            member_count=member_count if isinstance(member_count, int) else None,
            # Vanity — это бессрочное приглашение сервера без пригласившего.
            # Обычный инвайт заполняет хотя бы одно из этих полей.
            is_vanity=(
                invite_type in (INVITE_TYPE_GUILD, None)
                and expires_at is None
                and body.get("inviter") is None
                and bool(guild)
            ),
            expires_at=expires_at,
            invite_type=invite_type,
        )
