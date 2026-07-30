from datetime import datetime, timezone

import httpx
import pytest

from dcurl.api import (
    DISCORD_API_BASE,
    DiscordClient,
    Status,
    parse_timestamp,
    retry_after_seconds,
)
from dcurl.limiter import RateLimiter

TAKEN_BODY = {
    "code": "abc123",
    "expires_at": None,
    "inviter": None,
    "approximate_member_count": 4242,
    "guild": {"id": "999", "name": "Тестовый сервер"},
}
UNKNOWN_INVITE_BODY = {"message": "Unknown Invite", "code": 10006}


def make_client(handler, **kwargs):
    http = httpx.AsyncClient(
        base_url=DISCORD_API_BASE, transport=httpx.MockTransport(handler)
    )
    # Высокая скорость лимитера: тесты не должны ждать реального темпа.
    return http, DiscordClient(http, RateLimiter(10.0, burst=100), **kwargs)


async def test_200_means_taken_and_extracts_guild():
    http, client = make_client(lambda req: httpx.Response(200, json=TAKEN_BODY))
    async with http:
        result = await client.check("abc123")

    assert result.status is Status.TAKEN
    assert result.guild_name == "Тестовый сервер"
    assert result.guild_id == "999"
    assert result.member_count == 4242
    assert result.is_vanity is True
    assert result.is_conclusive


async def test_invite_with_inviter_is_not_vanity():
    body = {**TAKEN_BODY, "inviter": {"id": "1", "username": "someone"}}
    http, client = make_client(lambda req: httpx.Response(200, json=body))
    async with http:
        result = await client.check("abc123")

    assert result.status is Status.TAKEN
    assert result.is_vanity is False


async def test_404_from_discord_means_free():
    http, client = make_client(
        lambda req: httpx.Response(404, json=UNKNOWN_INVITE_BODY)
    )
    async with http:
        result = await client.check("abc123")

    assert result.status is Status.FREE


async def test_404_without_discord_body_is_not_treated_as_free():
    """Страница ошибки прокси не должна выглядеть как освободившийся код."""
    http, client = make_client(
        lambda req: httpx.Response(404, html="<html>not found</html>")
    )
    async with http:
        result = await client.check("abc123")

    assert result.status is Status.UNKNOWN
    assert not result.is_conclusive


@pytest.mark.parametrize("status_code", [500, 502, 503, 403])
async def test_server_and_auth_errors_are_unknown(status_code):
    http, client = make_client(lambda req: httpx.Response(status_code, json={}))
    async with http:
        result = await client.check("abc123")

    assert result.status is Status.UNKNOWN


async def test_network_failure_is_unknown_not_free():
    def handler(request):
        raise httpx.ConnectTimeout("нет сети", request=request)

    http, client = make_client(handler)
    async with http:
        result = await client.check("abc123")

    assert result.status is Status.UNKNOWN
    assert "ConnectTimeout" in (result.detail or "")


async def test_429_pauses_then_retries_successfully():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                json={"message": "You are being rate limited.", "retry_after": 0.01},
                headers={"x-ratelimit-scope": "global"},
            )
        return httpx.Response(200, json=TAKEN_BODY)

    seen = []
    http, client = make_client(
        handler, on_rate_limit=lambda code, delay, scope: seen.append((code, scope))
    )
    async with http:
        result = await client.check("abc123")

    assert calls["n"] == 2
    assert result.status is Status.TAKEN
    assert seen == [("abc123", "global")]


async def test_persistent_429_gives_up_as_unknown():
    def handler(request):
        return httpx.Response(429, json={"retry_after": 0.001})

    http, client = make_client(handler, max_retries=2)
    async with http:
        result = await client.check("abc123")

    assert result.status is Status.UNKNOWN
    assert "429" in (result.detail or "")


async def test_parses_expiry_and_invite_type():
    """Discord сам сообщает, когда приглашение истечёт — это ответ на вопрос
    «когда код освободится», гадать не нужно."""
    body = {
        "code": "abc123",
        "type": 2,
        "guild": None,
        "expires_at": "2026-07-30T17:51:45.690172+00:00",
        "inviter": {"id": "0", "global_name": "Кто-то"},
    }
    http, client = make_client(lambda req: httpx.Response(200, json=body))
    async with http:
        result = await client.check("abc123")

    assert result.status is Status.TAKEN
    assert result.expires_at == datetime(
        2026, 7, 30, 17, 51, 45, 690172, tzinfo=timezone.utc
    )
    assert result.invite_type == 2
    assert result.type_label == "дружеская ссылка"


async def test_null_guild_does_not_crash_and_is_not_vanity():
    """Код может занимать не сервер, а дружеская ссылка — поля guild нет."""
    body = {"code": "abc123", "type": 2, "guild": None, "expires_at": None,
            "inviter": None}
    http, client = make_client(lambda req: httpx.Response(200, json=body))
    async with http:
        result = await client.check("abc123")

    assert result.status is Status.TAKEN
    assert result.guild_name is None
    assert result.is_vanity is False


def test_parse_timestamp_handles_discord_and_zulu_formats():
    assert parse_timestamp("2026-07-30T17:51:45.690172+00:00") is not None
    assert parse_timestamp("2026-07-30T17:51:45Z") is not None
    assert parse_timestamp(None) is None
    assert parse_timestamp("не время") is None


def test_retry_after_prefers_json_body_over_header():
    response = httpx.Response(
        429, json={"retry_after": 1.75}, headers={"retry-after": "9"}
    )
    assert retry_after_seconds(response) == 1.75


def test_retry_after_falls_back_to_header_then_default():
    assert retry_after_seconds(httpx.Response(429, headers={"retry-after": "7"})) == 7.0
    assert retry_after_seconds(httpx.Response(429, text="nonsense")) > 0
