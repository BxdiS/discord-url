"""Полный цикл: настоящий DiscordClient и Watcher поверх мок-транспорта."""

import json

import httpx
import pytest

from dcurl.api import DISCORD_API_BASE, DiscordClient
from dcurl.config import PollingConfig
from dcurl.limiter import RateLimiter
from dcurl.scheduler import Watcher
from dcurl.state import Store

CODE = "vanitycode"

TAKEN_BODY = {
    "code": CODE,
    "type": 0,
    "expires_at": None,
    "inviter": None,
    "approximate_member_count": 1500,
    "guild": {"id": "42", "name": "Бустовый сервер"},
}
FREE_BODY = {"message": "Unknown Invite", "code": 10006}


class FakeDiscord:
    """Отдаёт 200, пока флаг `free` не поднят, потом 404."""

    def __init__(self) -> None:
        self.free = False
        self.requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.free:
            return httpx.Response(404, json=FREE_BODY)
        return httpx.Response(200, json=TAKEN_BODY)


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture
def polling():
    return PollingConfig(
        interval_seconds=0.01, jitter=0, confirmations=3, confirm_delay_seconds=0
    )


async def test_full_cycle_alerts_once_and_survives_restart(tmp_path, polling):
    discord = FakeDiscord()
    notifier = FakeNotifier()
    state, history = tmp_path / "state.json", tmp_path / "history.jsonl"

    def build():
        http = httpx.AsyncClient(
            base_url=DISCORD_API_BASE, transport=httpx.MockTransport(discord)
        )
        store = Store(state, history)
        store.load()
        watcher = Watcher(
            codes=[CODE],
            client=DiscordClient(http, RateLimiter(1000.0, burst=1000)),
            store=store,
            polling=polling,
            notifier=notifier,
        )
        return http, watcher

    http, watcher = build()
    async with http:
        await watcher.tick(CODE)
        assert notifier.sent == [], "занятый код не должен слать алерт"

        discord.free = True
        await watcher.tick(CODE)

    assert len(notifier.sent) == 1
    assert "Код свободен" in notifier.sent[0]
    assert "Бустовый сервер" in notifier.sent[0]

    # Перезапуск демона: состояние читается с диска, код всё ещё свободен.
    http2, watcher2 = build()
    async with http2:
        await watcher2.tick(CODE)

    assert len(notifier.sent) == 1, "после перезапуска алерт не должен повториться"

    entries = [
        json.loads(line)
        for line in history.read_text(encoding="utf-8").splitlines()
    ]
    assert [e["current"] for e in entries] == ["taken", "free"]
    assert entries[1]["previous"] == "taken"
    # Имя владельца взято из сохранённого состояния: ответ 404 его не содержит.
    assert entries[1]["guild_name"] == "Бустовый сервер"


async def test_transient_outage_between_checks_produces_no_alert(tmp_path, polling):
    """Сбой сети посреди наблюдения не должен выглядеть как освобождение."""
    notifier = FakeNotifier()
    responses = [
        httpx.Response(200, json=TAKEN_BODY),
        None,  # сетевая ошибка
        httpx.Response(200, json=TAKEN_BODY),
    ]

    def handler(request):
        item = responses.pop(0)
        if item is None:
            raise httpx.ConnectError("сеть пропала", request=request)
        return item

    http = httpx.AsyncClient(
        base_url=DISCORD_API_BASE, transport=httpx.MockTransport(handler)
    )
    store = Store(tmp_path / "state.json", tmp_path / "history.jsonl")
    watcher = Watcher(
        codes=[CODE],
        client=DiscordClient(http, RateLimiter(1000.0, burst=1000)),
        store=store,
        polling=polling,
        notifier=notifier,
    )

    async with http:
        for _ in range(3):
            await watcher.tick(CODE)

    assert notifier.sent == []
    # Один переход: первое обнаружение занятого кода. Сбой его не сдвинул.
    lines = (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
