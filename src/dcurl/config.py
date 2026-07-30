"""Загрузка конфига из TOML с переопределением из переменных окружения."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .proxy_parse import expand_port_range, parse_proxy, parse_proxy_file

# Discord разрешает 50 запросов/сек. Жёсткий потолок ниже лимита: даже если
# в конфиге написали ерунду, демон не должен устраивать шторм 429 — именно
# 429 (а не 404) ведёт к временному бану IP на стороне Cloudflare.
MAX_RATE_PER_SECOND = 10.0


class ConfigError(Exception):
    """Конфиг отсутствует, не парсится или содержит недопустимые значения."""


@dataclass(frozen=True)
class PollingConfig:
    interval_seconds: float = 60.0
    rate_limit_per_second: float = 1.0
    jitter: float = 0.2
    confirmations: int = 3
    confirm_delay_seconds: float = 2.0


@dataclass(frozen=True)
class ProxyConfig:
    urls: list[str] = ()  # список прокси-адресов для ротации
    # Если True, свежие свободные коды не проверяются N часов
    skip_free_hours: float = 12.0

    @property
    def enabled(self) -> bool:
        return bool(self.urls)


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass(frozen=True)
class Config:
    polling: PollingConfig
    telegram: TelegramConfig
    proxy: ProxyConfig = ProxyConfig()


def _validate(polling: PollingConfig) -> None:
    if polling.interval_seconds <= 0:
        raise ConfigError("polling.interval_seconds должен быть больше 0")
    if not 0 < polling.rate_limit_per_second <= MAX_RATE_PER_SECOND:
        raise ConfigError(
            f"polling.rate_limit_per_second должен быть в диапазоне "
            f"(0, {MAX_RATE_PER_SECOND}]"
        )
    if not 0 <= polling.jitter < 1:
        raise ConfigError("polling.jitter должен быть в диапазоне [0, 1)")
    if polling.confirmations < 1:
        raise ConfigError("polling.confirmations должен быть не меньше 1")
    if polling.confirm_delay_seconds < 0:
        raise ConfigError("polling.confirm_delay_seconds не может быть отрицательным")


def load_config(path: Path | None = None) -> Config:
    """Читает настройки опроса из TOML, секреты — из окружения.

    Отсутствующий файл не ошибка: берутся значения по умолчанию. Токен и
    chat_id в TOML не хранятся вообще — только в .env, который загружается
    в окружение через `bootstrap.load_dotenv()` до вызова этой функции.
    """
    raw: dict = {}
    if path is not None and path.exists():
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"не удалось разобрать {path}: {exc}") from exc

    raw_polling = raw.get("polling", {})
    try:
        polling = PollingConfig(
            interval_seconds=float(raw_polling.get("interval_seconds", 60.0)),
            rate_limit_per_second=float(raw_polling.get("rate_limit_per_second", 1.0)),
            jitter=float(raw_polling.get("jitter", 0.2)),
            confirmations=int(raw_polling.get("confirmations", 3)),
            confirm_delay_seconds=float(raw_polling.get("confirm_delay_seconds", 2.0)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"некорректное значение в секции [polling]: {exc}") from exc

    _validate(polling)

    telegram = TelegramConfig(
        bot_token=os.environ.get("TG_BOT_TOKEN", "").strip(),
        chat_id=os.environ.get("TG_CHAT_ID", "").strip(),
    )

    raw_proxy = raw.get("proxy", {})
    proxy_urls = raw_proxy.get("urls") or []

    if isinstance(proxy_urls, str):
        # Одна ссылка, путь к файлу или диапазон портов
        if proxy_urls.startswith(("http://", "https://", "socks5://", "socks4://")):
            # URL вид
            proxy_urls = [proxy_urls]
        elif ":" in proxy_urls and "-" in proxy_urls:
            # Попробовать как диапазон портов: host:start-end:user:pass
            expanded = expand_port_range(proxy_urls)
            if expanded:
                proxy_urls = expanded
            else:
                # Не получилось, может быть файл
                try:
                    content = Path(proxy_urls).read_text(encoding="utf-8")
                    proxy_urls = parse_proxy_file(content)
                except (FileNotFoundError, IsADirectoryError, PermissionError):
                    proxy_urls = []
        else:
            # Попробовать как файл
            try:
                content = Path(proxy_urls).read_text(encoding="utf-8")
                proxy_urls = parse_proxy_file(content)
            except (FileNotFoundError, IsADirectoryError, PermissionError):
                proxy_urls = []
    elif isinstance(proxy_urls, list):
        # Обработать каждый элемент
        processed = []
        for item in proxy_urls:
            if isinstance(item, str):
                parsed = parse_proxy(item)
                if parsed:
                    processed.append(parsed)
        proxy_urls = processed
    else:
        proxy_urls = []

    proxy = ProxyConfig(
        urls=proxy_urls,
        skip_free_hours=float(raw_proxy.get("skip_free_hours", 12.0)),
    )

    return Config(polling=polling, telegram=telegram, proxy=proxy)
