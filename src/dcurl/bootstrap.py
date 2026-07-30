"""Пути проекта, создание рабочих файлов из шаблонов и загрузка .env."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

# src/dcurl/bootstrap.py -> src/dcurl -> src -> корень репозитория.
# Берём корень от файла, а не от текущей директории, чтобы `python -m dcurl`
# работал из любого места, а не только из корня проекта.
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def project_root() -> Path:
    """Каталог с конфигами и состоянием. Переопределяется через DCURL_HOME."""
    override = os.environ.get("DCURL_HOME")
    return Path(override).expanduser().resolve() if override else _PACKAGE_ROOT


# шаблон -> рабочий файл
TEMPLATES: dict[str, str] = {
    "config.example.toml": "config.toml",
    ".env.example": ".env",
    "watchlist.example.txt": "watchlist.txt",
}


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config.toml"

    @property
    def env(self) -> Path:
        return self.root / ".env"

    @property
    def watchlist(self) -> Path:
        return self.root / "watchlist.txt"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def history(self) -> Path:
        return self.root / "history.jsonl"


def get_paths(root: Path | None = None) -> Paths:
    return Paths(root=root or project_root())


def ensure_working_files(root: Path | None = None) -> list[Path]:
    """Копирует каждый *.example в рабочий файл, если того ещё нет.

    Существующие файлы никогда не перезаписываются — иначе обновление
    репозитория затирало бы заполненный токен и watchlist. Возвращает список
    только что созданных файлов, чтобы CLI мог сказать, что нужно заполнить.
    """
    base = root or project_root()
    created: list[Path] = []

    for template_name, target_name in TEMPLATES.items():
        template = base / template_name
        target = base / target_name
        if target.exists() or not template.exists():
            continue
        shutil.copyfile(template, target)
        created.append(target)

    return created


def load_dotenv(path: Path | None = None) -> None:
    """Подгружает KEY=VALUE из .env в окружение процесса.

    Реальные переменные окружения имеют приоритет: .env их не перетирает,
    чтобы запуск с временным токеном в командной строке работал предсказуемо.
    """
    env_path = path or get_paths().env
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()

        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
