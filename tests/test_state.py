import json

import pytest

from dcurl.api import CheckResult, Status
from dcurl.state import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state.json", tmp_path / "history.jsonl")


def taken(code="abc123", **kwargs):
    return CheckResult(code=code, status=Status.TAKEN, guild_name="Сервер", **kwargs)


def free(code="abc123"):
    return CheckResult(code=code, status=Status.FREE)


def unknown(code="abc123"):
    return CheckResult(code=code, status=Status.UNKNOWN, detail="таймаут")


def test_first_sighting_is_a_transition(store):
    transition = store.record(taken())
    assert transition is not None
    assert transition.previous is None
    assert transition.current is Status.TAKEN
    assert transition.is_first_sighting


def test_same_status_twice_is_not_a_transition(store):
    store.record(taken())
    assert store.record(taken()) is None


def test_taken_to_free_is_a_transition_with_duration(store):
    store.record(taken())
    transition = store.record(free())

    assert transition is not None
    assert transition.previous is Status.TAKEN
    assert transition.current is Status.FREE
    assert transition.held_for is not None
    assert transition.held_for.total_seconds() >= 0


def test_unknown_never_changes_state(store):
    store.record(taken())
    assert store.record(unknown()) is None
    assert store.get("abc123").status is Status.TAKEN


def test_unknown_on_a_new_code_records_nothing(store):
    assert store.record(unknown()) is None
    assert store.get("abc123") is None


def test_reload_does_not_re_alert_for_a_known_status(tmp_path):
    state, history = tmp_path / "state.json", tmp_path / "history.jsonl"

    first = Store(state, history)
    first.record(free())
    first.save()

    second = Store(state, history)
    second.load()
    # Код по-прежнему свободен — повторного алерта быть не должно.
    assert second.record(free()) is None


def test_forget_drops_codes_absent_from_watchlist(store):
    store.record(taken("abc123"))
    store.record(taken("xyz789"))
    store.forget({"abc123"})

    assert store.get("abc123") is not None
    assert store.get("xyz789") is None


def test_history_appends_one_line_per_transition(store, tmp_path):
    store.append_history(store.record(taken()))
    store.append_history(store.record(free()))

    lines = (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["current"] == "free"
    assert json.loads(lines[1])["previous"] == "taken"


def test_saved_state_is_valid_json(store, tmp_path):
    store.record(taken())
    store.save()

    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["codes"]["abc123"]["status"] == "taken"


def test_corrupt_state_file_does_not_raise(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{ это не json", encoding="utf-8")

    store = Store(state, tmp_path / "history.jsonl")
    store.load()  # не должно бросить исключение
    assert store.get("abc123") is None
