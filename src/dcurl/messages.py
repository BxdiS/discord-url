"""Тексты уведомлений (HTML для Telegram)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape

from .api import CheckResult, Status
from .invite import invite_url
from .state import Transition


def format_expiry(expires_at: datetime | None) -> str | None:
    """«освободится 30.07.2026 17:51 UTC (через 42 мин)» либо None."""
    if expires_at is None:
        return None
    remaining = expires_at - datetime.now(timezone.utc)
    stamp = expires_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    if remaining.total_seconds() <= 0:
        return f"срок истёк ({stamp}), код вот-вот освободится"
    return f"освободится {stamp} (через {human_duration(remaining)})"


def describe_taken(result: CheckResult) -> str:
    """Однострочное описание того, чем занят код."""
    bits = [result.guild_name or result.type_label]
    if result.member_count is not None:
        bits.append(f"~{result.member_count} участников".replace(",", " "))
    if result.is_vanity:
        bits.append("vanity, бессрочно")
    expiry = format_expiry(result.expires_at)
    if expiry:
        bits.append(expiry)
    return ", ".join(bits)


def human_duration(delta: timedelta | None) -> str:
    if delta is None:
        return "неизвестно сколько"
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total} с"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60

    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes and not days:
        parts.append(f"{minutes} мин")
    return " ".join(parts) or "меньше минуты"


def _link(code: str) -> str:
    return f'<a href="{invite_url(code)}">discord.gg/{escape(code)}</a>'


def build_transition_message(transition: Transition) -> str | None:
    """Текст алерта для перехода. None — если сообщать не о чем."""
    code = transition.code
    result = transition.result

    if transition.current is Status.FREE:
        lines = [f"🟢 <b>Код свободен:</b> {_link(code)}"]
        if transition.is_first_sighting:
            lines.append("Был свободен уже на момент добавления в watchlist.")
        else:
            lines.append(f"Был занят: {human_duration(transition.held_for)}.")
            if transition.guild_name:
                lines.append(f"Прежний владелец: {escape(transition.guild_name)}")
        lines.append("")
        lines.append(
            "Забрать: Настройки сервера → Пользовательская ссылка-приглашение "
            "(нужен Level 3, 14 бустов)."
        )
        return "\n".join(lines)

    if transition.current is Status.TAKEN and transition.previous is Status.FREE:
        owner = escape(result.guild_name) if result.guild_name else "неизвестный сервер"
        held = human_duration(transition.held_for)
        return (
            f"🔴 <b>Код заняли:</b> {_link(code)}\n"
            f"Новый владелец: {owner}\n"
            f"Был свободен: {held}."
        )

    # Первое наблюдение занятого кода — обычное дело, тревожить незачем.
    return None


def build_startup_message(codes: list[str], interval: float) -> str:
    listed = "\n".join(f"• {_link(code)}" for code in codes[:20])
    more = f"\n…и ещё {len(codes) - 20}" if len(codes) > 20 else ""
    return (
        f"👀 <b>Watcher запущен</b>\n"
        f"Отслеживается кодов: {len(codes)}, интервал {interval:.0f} с.\n\n"
        f"{listed}{more}"
    )
