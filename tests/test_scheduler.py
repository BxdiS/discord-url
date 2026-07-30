from dataclasses import replace
from datetime import timedelta

import pytest

from dcurl.api import CheckResult, Status
from dcurl.config import PollingConfig
from dcurl.scheduler import Watcher
from dcurl.state import Store, utcnow

CODE = "abc123"

TAKEN = CheckResult(code=CODE, status=Status.TAKEN, guild_name="Сервер")
FREE = CheckResult(code=CODE, status=Status.FREE)
UNKNOWN = CheckResult(code=CODE, status=Status.UNKNOWN, detail="таймаут")


class FakeClient:
    """Отдаёт заранее заданные результаты; последний повторяется."""

    def __init__(self, *results: CheckResult) -> None:
        assert results
        self._results = list(results)
        self.calls = 0

    async def check(self, code: str) -> CheckResult:
        self.calls += 1
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0]


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture
def polling():
    # Два подтверждения без пауз — тесты не должны спать.
    return PollingConfig(confirmations=2, confirm_delay_seconds=0)


def make_watcher(tmp_path, client, polling, notifier=None):
    store = Store(tmp_path / "state.json", tmp_path / "history.jsonl")
    return Watcher(
        codes=[CODE],
        client=client,
        store=store,
        polling=polling,
        notifier=notifier,
    ), store


async def test_confirmed_free_sends_exactly_one_alert(tmp_path, polling):
    client = FakeClient(FREE)
    notifier = FakeNotifier()
    watcher, store = make_watcher(tmp_path, client, polling, notifier)

    await watcher.tick(CODE)

    assert client.calls == 2  # первичная проверка + одно подтверждение
    assert len(notifier.sent) == 1
    assert "Код свободен" in notifier.sent[0]
    assert store.get(CODE).status is Status.FREE


async def test_unconfirmed_free_sends_nothing(tmp_path, polling):
    """Одиночный 404, который не подтвердился, — не повод будить владельца."""
    client = FakeClient(FREE, TAKEN)
    notifier = FakeNotifier()
    watcher, store = make_watcher(tmp_path, client, polling, notifier)

    await watcher.tick(CODE)

    assert notifier.sent == []
    assert store.get(CODE).status is Status.TAKEN


async def test_free_followed_by_unknown_is_not_an_alert(tmp_path, polling):
    client = FakeClient(FREE, UNKNOWN)
    notifier = FakeNotifier()
    watcher, store = make_watcher(tmp_path, client, polling, notifier)

    await watcher.tick(CODE)

    assert notifier.sent == []
    assert store.get(CODE) is None


async def test_unknown_never_alerts_and_never_writes_history(tmp_path, polling):
    client = FakeClient(UNKNOWN)
    notifier = FakeNotifier()
    watcher, _ = make_watcher(tmp_path, client, polling, notifier)

    await watcher.tick(CODE)

    assert notifier.sent == []
    assert not (tmp_path / "history.jsonl").exists()


async def test_first_sighting_of_a_taken_code_is_silent(tmp_path, polling):
    client = FakeClient(TAKEN)
    notifier = FakeNotifier()
    watcher, store = make_watcher(tmp_path, client, polling, notifier)

    await watcher.tick(CODE)

    assert notifier.sent == []
    assert store.get(CODE).status is Status.TAKEN


async def test_code_getting_claimed_is_reported(tmp_path, polling):
    client = FakeClient(FREE, FREE, TAKEN)
    notifier = FakeNotifier()
    watcher, _ = make_watcher(tmp_path, client, polling, notifier)

    await watcher.tick(CODE)  # свободен и подтверждён -> алерт
    await watcher.tick(CODE)  # кто-то занял -> второй алерт

    assert len(notifier.sent) == 2
    assert "Код свободен" in notifier.sent[0]
    assert "Код заняли" in notifier.sent[1]


def taken_expiring_in(seconds: float) -> CheckResult:
    return CheckResult(
        code=CODE,
        status=Status.TAKEN,
        expires_at=utcnow() + timedelta(seconds=seconds),
    )


def test_next_poll_lands_just_after_a_known_expiry(tmp_path, polling):
    """Если Discord назвал срок истечения, ждём до него, а не весь интервал."""
    slow = replace(polling, interval_seconds=600, jitter=0)
    watcher, _ = make_watcher(tmp_path, FakeClient(TAKEN), slow)

    delay = watcher._next_delay(taken_expiring_in(30))

    assert 30 < delay <= 40


def test_far_future_expiry_does_not_stretch_the_interval(tmp_path, polling):
    slow = replace(polling, interval_seconds=60, jitter=0)
    watcher, _ = make_watcher(tmp_path, FakeClient(TAKEN), slow)

    assert watcher._next_delay(taken_expiring_in(7 * 86400)) == 60


def test_already_expired_invite_is_rechecked_promptly(tmp_path, polling):
    slow = replace(polling, interval_seconds=600, jitter=0)
    watcher, _ = make_watcher(tmp_path, FakeClient(TAKEN), slow)

    assert watcher._next_delay(taken_expiring_in(-5)) <= 5.0


def test_delay_falls_back_to_interval_without_expiry(tmp_path, polling):
    slow = replace(polling, interval_seconds=45, jitter=0)
    watcher, _ = make_watcher(tmp_path, FakeClient(TAKEN), slow)

    assert watcher._next_delay(TAKEN) == 45
    assert watcher._next_delay(None) == 45


def test_skip_free_hours_when_enabled(tmp_path, polling):
    client = FakeClient(FREE)
    store = Store(tmp_path / "state.json", tmp_path / "history.jsonl")
    watcher = Watcher(
        codes=[CODE],
        client=client,
        store=store,
        polling=polling,
        skip_free_hours=12.0,
    )
    store.record(FREE)

    assert watcher._should_skip(CODE)


def test_dont_skip_free_when_skip_disabled(tmp_path, polling):
    client = FakeClient(FREE)
    store = Store(tmp_path / "state.json", tmp_path / "history.jsonl")
    watcher = Watcher(
        codes=[CODE],
        client=client,
        store=store,
        polling=polling,
        skip_free_hours=0,
    )
    store.record(FREE)

    assert not watcher._should_skip(CODE)


def test_skip_eventually_expires(tmp_path, polling):
    from datetime import timedelta

    client = FakeClient(FREE)
    store = Store(tmp_path / "state.json", tmp_path / "history.jsonl")
    watcher = Watcher(
        codes=[CODE],
        client=client,
        store=store,
        polling=polling,
        skip_free_hours=0.0001,  # примерно 0.36 секунды
    )
    store.record(FREE)

    # Свежий код пропускаем
    assert watcher._should_skip(CODE)

    # Но статус пишется "с давно"
    state = store.get(CODE)
    state.since = state.since - timedelta(hours=1)
    store._states[CODE] = state

    # Теперь не пропускаем
    assert not watcher._should_skip(CODE)


async def test_already_free_code_is_not_reconfirmed_every_cycle(tmp_path, polling):
    """Подтверждения нужны только на переходе — иначе это лишние запросы."""
    client = FakeClient(FREE)
    watcher, _ = make_watcher(tmp_path, client, polling)

    await watcher.tick(CODE)
    calls_after_transition = client.calls  # 1 проверка + 1 подтверждение
    await watcher.tick(CODE)

    assert calls_after_transition == 2
    assert client.calls == 3, "второй цикл должен стоить один запрос"


async def test_notifier_failure_does_not_break_the_loop(tmp_path, polling):
    class BrokenNotifier:
        async def send(self, text: str) -> None:
            raise RuntimeError("telegram недоступен")

    client = FakeClient(FREE)
    watcher, store = make_watcher(tmp_path, client, polling, BrokenNotifier())

    await watcher.tick(CODE)  # не должно бросить

    # Переход всё равно зафиксирован, иначе алерт ушёл бы повторно.
    assert store.get(CODE).status is Status.FREE
