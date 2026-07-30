"""Командный интерфейс dcurl."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .api import DiscordClient, Status, build_client
from .bootstrap import Paths, ensure_working_files, get_paths, load_dotenv
from .config import Config, ConfigError, load_config
from .proxy import ProxyRotator
from .invite import InviteParseError, parse_code, parse_watchlist
from .limiter import RateLimiter
from .messages import build_startup_message, describe_taken
from .notify import TelegramError, TelegramNotifier
from .scheduler import Watcher
from .state import Store

log = logging.getLogger("dcurl")

STATUS_LABEL = {
    Status.FREE: "СВОБОДЕН",
    Status.TAKEN: "занят",
    Status.UNKNOWN: "неизвестно",
}


def setup_logging(verbose: bool) -> None:
    # На Windows потоки по умолчанию берут кодировку системной локали, и
    # русский текст превращается в мусор, как только вывод перенаправлен
    # в файл или в пайп. UTF-8 фиксируем явно.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dcurl",
        description="Отслеживает, когда освобождается Discord invite/vanity код.",
    )
    parser.add_argument("--version", action="version", version=f"dcurl {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")

    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="разовая проверка кодов и выход")
    check.add_argument("targets", nargs="+", metavar="ССЫЛКА|КОД")

    watch = sub.add_parser("watch", help="следить постоянно и слать алерты")
    watch.add_argument(
        "targets",
        nargs="*",
        metavar="ССЫЛКА|КОД",
        help="если не указано — берётся watchlist.txt",
    )
    watch.add_argument(
        "--interval",
        type=float,
        default=None,
        help="переопределить интервал опроса, секунды",
    )

    sub.add_parser("test-notify", help="отправить тестовое сообщение в Telegram")
    sub.add_parser("init", help="создать рабочие файлы из шаблонов и выйти")

    return parser


def _resolve_targets(targets: list[str]) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for raw in targets:
        code = parse_code(raw)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _load_watchlist(path: Path) -> list[str]:
    if not path.exists():
        raise InviteParseError(f"нет файла {path.name} — запустите `dcurl init`")
    return parse_watchlist(path.read_text(encoding="utf-8"))


def _report_created(created: list[Path]) -> None:
    if not created:
        return
    print("Созданы рабочие файлы из шаблонов:")
    for path in created:
        print(f"  • {path.name}")
    print(
        "\nЗаполните .env (TG_BOT_TOKEN, TG_CHAT_ID) и watchlist.txt, "
        "затем запустите снова.\nЭти файлы в git не попадают."
    )


def _make_notifier(config: Config) -> TelegramNotifier | None:
    if not config.telegram.enabled:
        return None
    return TelegramNotifier(config.telegram.bot_token, config.telegram.chat_id)


async def cmd_check(config: Config, targets: list[str]) -> int:
    codes = _resolve_targets(targets)
    limiter = RateLimiter(config.polling.rate_limit_per_second)

    async with build_client() as http:
        client = DiscordClient(
            http,
            limiter,
            on_rate_limit=lambda code, delay, scope: log.warning(
                "[%s] лимит Discord (scope=%s), пауза %.1f с", code, scope, delay
            ),
        )
        results = [await client.check(code) for code in codes]

    width = max(len(code) for code in codes) + 2
    all_free = True
    for result in results:
        extra = ""
        if result.status is Status.TAKEN:
            all_free = False
            extra = " — " + describe_taken(result)
        elif result.status is Status.UNKNOWN:
            all_free = False
            extra = f" — {result.detail}"
        print(
            f"discord.gg/{result.code:<{width}} {STATUS_LABEL[result.status]}{extra}"
        )

    return 0 if all_free else 1


async def cmd_watch(config: Config, paths: Paths, targets: list[str]) -> int:
    codes = _resolve_targets(targets) if targets else _load_watchlist(paths.watchlist)
    if not codes:
        log.error("Список кодов пуст — добавьте строки в %s", paths.watchlist.name)
        return 2

    notifier = _make_notifier(config)
    if notifier is None:
        log.warning(
            "Telegram не настроен (TG_BOT_TOKEN / TG_CHAT_ID в .env пусты) — "
            "алерты будут только в этой консоли."
        )

    store = Store(paths.state, paths.history)
    limiter = RateLimiter(config.polling.rate_limit_per_second)
    proxy_rotator = ProxyRotator(config.proxy.urls)

    msg = f"Отслеживается кодов: {len(codes)}, интервал {config.polling.interval_seconds:.0f} с, лимит {config.polling.rate_limit_per_second:.2f} запр/с"
    if proxy_rotator.enabled:
        msg += f", прокси: {len(config.proxy.urls)}"
    if config.proxy.skip_free_hours > 0:
        msg += f", пропуск свободных: {config.proxy.skip_free_hours:.0f} ч"
    log.info(msg)

    proxy_url = config.proxy.urls[0] if config.proxy.urls else None
    async with build_client(proxy=proxy_url) as http:
        client = DiscordClient(
            http,
            limiter,
            on_rate_limit=lambda code, delay, scope: log.warning(
                "[%s] лимит Discord (scope=%s), пауза %.1f с", code, scope, delay
            ),
        )
        watcher = Watcher(
            codes=codes,
            client=client,
            store=store,
            polling=config.polling,
            notifier=notifier,
            skip_free_hours=config.proxy.skip_free_hours,
        )

        try:
            if notifier is not None:
                try:
                    await notifier.send(
                        build_startup_message(codes, config.polling.interval_seconds)
                    )
                except TelegramError as exc:
                    log.error("Telegram отклонил стартовое сообщение: %s", exc)
                    log.error("Исправьте .env и запустите снова.")
                    return 2
            await watcher.run()
        except (KeyboardInterrupt, asyncio.CancelledError):
            log.info("Остановлено, состояние сохранено.")
        finally:
            if notifier is not None:
                await notifier.aclose()

    return 0


async def cmd_test_notify(config: Config) -> int:
    if not config.telegram.enabled:
        log.error(
            "Telegram не настроен: заполните TG_BOT_TOKEN и TG_CHAT_ID в .env"
        )
        return 2

    notifier = _make_notifier(config)
    assert notifier is not None
    try:
        username = await notifier.whoami()
        await notifier.send(
            "✅ <b>dcurl на связи.</b>\nЕсли вы это видите — уведомления работают."
        )
        print(f"Отправлено. Бот: @{username}")
        return 0
    except TelegramError as exc:
        log.error("Telegram отклонил запрос: %s", exc)
        return 2
    finally:
        await notifier.aclose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    paths = get_paths()
    created = ensure_working_files(paths.root)
    load_dotenv(paths.env)

    if args.command == "init":
        if created:
            _report_created(created)
        else:
            print("Все рабочие файлы уже на месте, ничего не меняю.")
        return 0

    if created:
        _report_created(created)
        # `check` ничего не требует от конфига — пусть работает сразу.
        if args.command in {"watch", "test-notify"}:
            return 0
        print()

    try:
        config = load_config(paths.config)
    except ConfigError as exc:
        log.error("Ошибка конфига: %s", exc)
        return 2

    if args.command == "watch" and args.interval is not None:
        if args.interval <= 0:
            log.error("--interval должен быть больше 0")
            return 2
        config = replace(
            config, polling=replace(config.polling, interval_seconds=args.interval)
        )

    try:
        if args.command == "check":
            return asyncio.run(cmd_check(config, args.targets))
        if args.command == "watch":
            return asyncio.run(cmd_watch(config, paths, args.targets))
        if args.command == "test-notify":
            return asyncio.run(cmd_test_notify(config))
    except InviteParseError as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 130

    return 2
