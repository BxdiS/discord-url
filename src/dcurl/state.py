"""Сохранение статусов между запусками и журнал переходов."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .api import CheckResult, Status


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CodeState:
    status: Status
    since: datetime  # когда статус стал текущим
    last_checked: datetime
    guild_name: str | None = None

    def to_json(self) -> dict:
        return {
            "status": self.status.value,
            "since": self.since.isoformat(),
            "last_checked": self.last_checked.isoformat(),
            "guild_name": self.guild_name,
        }

    @classmethod
    def from_json(cls, data: dict) -> CodeState:
        return cls(
            status=Status(data["status"]),
            since=datetime.fromisoformat(data["since"]),
            last_checked=datetime.fromisoformat(data["last_checked"]),
            guild_name=data.get("guild_name"),
        )


@dataclass(frozen=True)
class Transition:
    code: str
    previous: Status | None
    current: Status
    at: datetime
    held_for: timedelta | None
    result: CheckResult
    # Ответ 404 не несёт данных о сервере, поэтому имя прежнего владельца
    # берётся из сохранённого состояния — иначе алерт был бы безымянным.
    previous_guild_name: str | None = None

    @property
    def is_first_sighting(self) -> bool:
        return self.previous is None

    @property
    def guild_name(self) -> str | None:
        return self.result.guild_name or self.previous_guild_name


class Store:
    """Держит последний известный статус каждого кода.

    Нужен ровно для одного: после перезапуска демон не должен повторно
    сообщать об освобождении кода, про который уже сообщал.
    """

    def __init__(self, state_path: Path, history_path: Path) -> None:
        self._state_path = state_path
        self._history_path = history_path
        self._states: dict[str, CodeState] = {}

    def load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Битый файл состояния не должен ронять демона: хуже всего,
            # что случится — один повторный алерт.
            return

        for code, data in (raw.get("codes") or {}).items():
            try:
                self._states[code] = CodeState.from_json(data)
            except (KeyError, ValueError):
                continue

    def get(self, code: str) -> CodeState | None:
        return self._states.get(code)

    def record(self, result: CheckResult) -> Transition | None:
        """Обновляет состояние по результату проверки.

        Возвращает переход, если статус изменился (или код виден впервые),
        иначе None. Результаты UNKNOWN состояние не меняют — временный сбой
        Discord не должен выглядеть как смена статуса.
        """
        now = utcnow()
        if not result.is_conclusive:
            existing = self._states.get(result.code)
            if existing is not None:
                existing.last_checked = now
            return None

        previous = self._states.get(result.code)
        if previous is not None and previous.status is result.status:
            previous.last_checked = now
            if result.guild_name:
                previous.guild_name = result.guild_name
            return None

        transition = Transition(
            code=result.code,
            previous=previous.status if previous else None,
            current=result.status,
            at=now,
            held_for=(now - previous.since) if previous else None,
            result=result,
            previous_guild_name=previous.guild_name if previous else None,
        )

        self._states[result.code] = CodeState(
            status=result.status,
            since=now,
            last_checked=now,
            guild_name=result.guild_name or (previous.guild_name if previous else None),
        )
        return transition

    def forget(self, codes: set[str]) -> None:
        """Убирает из состояния коды, которых больше нет в watchlist."""
        for code in list(self._states):
            if code not in codes:
                del self._states[code]

    def save(self) -> None:
        payload = {
            "version": 1,
            "saved_at": utcnow().isoformat(),
            "codes": {code: st.to_json() for code, st in self._states.items()},
        }
        _atomic_write_json(self._state_path, payload)

    def append_history(self, transition: Transition) -> None:
        entry = {
            "at": transition.at.isoformat(),
            "code": transition.code,
            "previous": transition.previous.value if transition.previous else None,
            "current": transition.current.value,
            "held_for_seconds": (
                round(transition.held_for.total_seconds(), 3)
                if transition.held_for
                else None
            ),
            "guild_name": transition.guild_name,
            "guild_id": transition.result.guild_id,
            "member_count": transition.result.member_count,
            "is_vanity": transition.result.is_vanity,
        }
        with self._history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Пишет во временный файл и подменяет им целевой.

    Прямая запись оставила бы обрезанный state.json, если процесс убьют
    посреди сохранения, и демон потерял бы всю историю статусов.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
