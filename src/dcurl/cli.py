"""Командный интерфейс dcurl."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .api import CheckResult, DiscordClient, Status, build_client
from .bootstrap import Paths, ensure_working_files, get_paths, load_dotenv
from .config import Config, ConfigError, load_config
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


def _make_notifier(config: Config, proxy: str | None = None) -> TelegramNotifier | None:
    if not config.telegram.enabled:
        return None

    use_proxy_for_tg = getattr(config.proxy, 'use_for_telegram', True)
    tg_proxy = proxy if use_proxy_for_tg else None
    if tg_proxy:
        log.info(f"Telegram будет использовать прокси")
    else:
        log.info(f"Telegram работает БЕЗ прокси")

    return TelegramNotifier(
        config.telegram.bot_token,
        config.telegram.chat_id,
        proxy=tg_proxy,
    )


async def _check_one_proxy(proxy: str, test_url: str, timeout: float) -> tuple[str, bool]:
    """Быстрая проверка одной прокси. Возвращает (proxy, is_working)."""
    import httpx
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            proxy=proxy,
            follow_redirects=False,
        ) as http:
            response = await http.get(test_url)
            if response.status_code < 500:
                return (proxy, True)
    except Exception:
        pass
    return (proxy, False)


async def _find_working_proxy(
    proxies: list[str],
    test_url: str = "https://discord.com/api/v10/invites/test",
    timeout: float = 3.0,
    max_needed: int = 10,
) -> tuple[str | None, list[str]]:
    """Параллельно проверяет все прокси. Возвращает (working_proxy, all_working_proxies)."""
    if not proxies:
        return None, []

    log.info(f"Параллельная проверка {len(proxies)} прокси (timeout {timeout}s)...")

    all_working = []
    batch_size = 50

    for batch_start in range(0, len(proxies), batch_size):
        batch = proxies[batch_start:batch_start + batch_size]
        tasks = [_check_one_proxy(proxy, test_url, timeout) for proxy in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, tuple) and result[1]:
                all_working.append(result[0])

        # Логируем прогресс батча
        log.info(f"  Проверено {min(batch_start + batch_size, len(proxies))}/{len(proxies)}, найдено рабочих: {len(all_working)}")

        # Если уже нашли достаточно - останавливаемся
        if len(all_working) >= max_needed:
            break

    if all_working:
        log.info(f"✓ Найдено {len(all_working)} рабочих прокси из {len(proxies)}")
        return all_working[0], all_working
    else:
        log.warning(f"✗ Ни одна прокси из {len(proxies)} не отвечает!")
        return None, []


class ProxyRotatingClient:
    """Обёртка над DiscordClient с автоматической ротацией прокси при ошибках."""

    def __init__(
        self, base_config: Config, limiter: RateLimiter, on_rate_limit=None
    ) -> None:
        self._base_config = base_config
        self._limiter = limiter
        self._on_rate_limit = on_rate_limit
        self._proxies = base_config.proxy.urls if base_config.proxy.urls else [None]
        self._proxy_idx = 0
        self._http = None
        self._client = None

    async def _init_proxy(self) -> None:
        """Инициализирует клиент с текущей прокси."""
        if self._http:
            await self._http.aclose()
        proxy = self._proxies[self._proxy_idx % len(self._proxies)]
        self._http = build_client(proxy=proxy)
        self._client = DiscordClient(
            self._http,
            self._limiter,
            on_rate_limit=self._on_rate_limit,
        )

    async def check(self, code: str) -> CheckResult:
        """Проверяет код с ротацией прокси при ошибках."""
        if not self._client:
            await self._init_proxy()

        for attempt in range(len(self._proxies)):
            try:
                result = await self._client.check(code)

                if result.status != Status.UNKNOWN:
                    return result

                if "Timeout" in (result.detail or ""):
                    self._proxy_idx += 1
                    await self._init_proxy()
                    continue

                return result
            except Exception as e:
                self._proxy_idx += 1
                if attempt < len(self._proxies) - 1:
                    await self._init_proxy()
                else:
                    return CheckResult(
                        code=code,
                        status=Status.UNKNOWN,
                        detail="все прокси не работают",
                    )

        return CheckResult(
            code=code,
            status=Status.UNKNOWN,
            detail="не удалось подключиться",
        )

    async def aclose(self) -> None:
        """Закрывает HTTP клиент."""
        if self._http:
            await self._http.aclose()


async def cmd_check(config: Config, targets: list[str]) -> int:
    codes = _resolve_targets(targets)
    limiter = RateLimiter(config.polling.rate_limit_per_second)

    # Health-check прокси - находим рабочую
    working_proxy = None
    if config.proxy.urls:
        working_proxy, _ = await _find_working_proxy(list(config.proxy.urls))
        if not working_proxy:
            log.error("Ни одна прокси не работает!")
            return 1

    results = []
    async with build_client(proxy=working_proxy) as http:
        client = DiscordClient(
            http,
            limiter,
            on_rate_limit=lambda c, delay, scope: log.warning(
                "[%s] лимит Discord (scope=%s), пауза %.1f с", c, scope, delay
            ),
        )
        for code in codes:
            result = await client.check(code)
            results.append(result)

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

    store = Store(paths.state, paths.history)
    limiter = RateLimiter(config.polling.rate_limit_per_second)

    msg = f"Отслеживается кодов: {len(codes)}, интервал {config.polling.interval_seconds:.0f} с, лимит {config.polling.rate_limit_per_second:.2f} запр/с"
    if config.proxy.urls:
        msg += f", прокси: {len(config.proxy.urls)} с health-check"
    if config.polling.skip_free_hours > 0:
        msg += f", пропуск свободных: {config.polling.skip_free_hours:.0f} ч"
    log.info(msg)

    # Health-check прокси и выбор рабочей
    initial_proxy = None
    working_proxies = []
    if config.proxy.urls:
        initial_proxy, working_proxies = await _find_working_proxy(list(config.proxy.urls))
        if not initial_proxy:
            log.error("Не найдено рабочих прокси! Демон не запустится.")
            log.error("Проверьте прокси в proxies.txt или удалите их для работы напрямую.")
            return 1

    # Создаём Telegram notifier (с прокси если требуется)
    notifier = _make_notifier(config, proxy=initial_proxy)
    if notifier is None:
        log.warning(
            "Telegram не настроен (TG_BOT_TOKEN / TG_CHAT_ID в .env пусты) — "
            "алерты будут только в этой консоли."
        )

    async with build_client(proxy=initial_proxy) as http:
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
            skip_free_hours=config.polling.skip_free_hours,
        )

        try:
            # Стартовое сообщение в Telegram - с таймаутом чтобы не блокировать демон
            if notifier is not None:
                try:
                    log.info("Отправка стартового сообщения в Telegram (таймаут 5с)...")
                    await asyncio.wait_for(
                        notifier.send(
                            build_startup_message(codes, config.polling.interval_seconds)
                        ),
                        timeout=5.0,
                    )
                    log.info("✓ Telegram работает, уведомления включены")
                except (TelegramError, asyncio.TimeoutError, Exception) as exc:
                    log.warning("⚠ Telegram недоступен: %s", type(exc).__name__)
                    log.warning("Демон продолжит работать БЕЗ уведомлений в Telegram")
                    # Отключаем notifier чтобы watcher его не использовал
                    await notifier.aclose()
                    notifier = None
                    watcher._notifier = None

            log.info("=" * 60)
            log.info("Демон запущен, начинаю проверку кодов...")
            log.info("=" * 60)
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

    # Health-check прокси для Telegram
    working_proxy = None
    use_proxy_for_tg = getattr(config.proxy, 'use_for_telegram', True)
    if use_proxy_for_tg and config.proxy.urls:
        working_proxy, _ = await _find_working_proxy(list(config.proxy.urls))
        if not working_proxy:
            log.warning("Прокси не работают, попробую напрямую...")

    notifier = _make_notifier(config, proxy=working_proxy)
    assert notifier is not None
    try:
        log.info("Отправка тестового сообщения (таймаут 10с)...")
        username = await asyncio.wait_for(notifier.whoami(), timeout=10.0)
        await asyncio.wait_for(
            notifier.send(
                "✅ <b>dcurl на связи.</b>\nЕсли вы это видите — уведомления работают."
            ),
            timeout=10.0,
        )
        print(f"Отправлено. Бот: @{username}")
        return 0
    except asyncio.TimeoutError:
        log.error("Telegram не ответил за 10 секунд - прокси не поддерживает HTTPS")
        return 2
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
