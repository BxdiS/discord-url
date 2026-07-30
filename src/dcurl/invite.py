"""Разбор и нормализация ссылок-приглашений Discord."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Vanity-код: 2–32 символа, буквы/цифры/дефис. Обычные инвайт-коды короче и
# состоят только из букв и цифр, так что этот диапазон покрывает оба вида.
CODE_RE = re.compile(r"^[A-Za-z0-9-]{2,32}$")

# Хосты, где путь — сразу код: discord.gg/CODE
_SHORT_HOSTS = {"discord.gg"}

# Хосты, где код лежит под /invite/: discord.com/invite/CODE
_LONG_HOSTS = {"discord.com", "discordapp.com"}

# Поддомены окружений Discord, которые не влияют на разбор.
_SUBDOMAIN_RE = re.compile(r"^(?:www|ptb|canary)\.", re.IGNORECASE)


class InviteParseError(ValueError):
    """Строка не является распознаваемой ссылкой или кодом приглашения."""


def parse_code(raw: str) -> str:
    """Извлекает голый код приглашения из любой поддерживаемой формы.

    Принимает `discord.com/invite/CODE`, `https://discord.gg/CODE?x=1`,
    `discordapp.com/invite/CODE` и голый `CODE`.

    Регистр сохраняется: обычные инвайт-коды регистрозависимы, приведение
    к нижнему регистру превратило бы валидный код в несуществующий.
    """
    text = raw.strip()
    if not text:
        raise InviteParseError("пустая строка")

    if "/" in text or "." in text:
        code = _code_from_url(text)
    else:
        code = text

    if not CODE_RE.match(code):
        raise InviteParseError(
            f"{code!r} не похож на код приглашения "
            "(ожидается 2–32 символа: буквы, цифры, дефис)"
        )
    return code


def _code_from_url(text: str) -> str:
    candidate = text if "//" in text else f"https://{text}"
    parts = urlsplit(candidate)

    host = _SUBDOMAIN_RE.sub("", parts.hostname or "").lower()
    segments = [seg for seg in parts.path.split("/") if seg]

    if host in _SHORT_HOSTS:
        # discord.gg/CODE, а также разрешённая форма discord.gg/invite/CODE
        if segments and segments[0].lower() == "invite":
            segments = segments[1:]
    elif host in _LONG_HOSTS:
        if not segments or segments[0].lower() != "invite":
            raise InviteParseError(
                f"{text!r}: у {host} ссылка-приглашение должна иметь вид "
                f"{host}/invite/КОД"
            )
        segments = segments[1:]
    else:
        raise InviteParseError(
            f"{text!r}: неизвестный хост {host!r}; "
            "поддерживаются discord.com, discord.gg, discordapp.com"
        )

    if not segments:
        raise InviteParseError(f"{text!r}: в ссылке нет кода приглашения")
    return segments[0]


def invite_url(code: str) -> str:
    """Каноническая короткая ссылка для кода."""
    return f"https://discord.gg/{code}"


def parse_watchlist(text: str) -> list[str]:
    """Разбирает watchlist-файл в список кодов без дублей, сохраняя порядок.

    Пустые строки и строки, начинающиеся с `#`, игнорируются. Некорректная
    строка приводит к ошибке с номером строки — молча пропускать нельзя,
    иначе можно годами «следить» за кодом, который никто не проверяет.
    """
    codes: list[str] = []
    seen: set[str] = set()

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            code = parse_code(stripped)
        except InviteParseError as exc:
            raise InviteParseError(f"строка {lineno}: {exc}") from exc
        if code not in seen:
            seen.add(code)
            codes.append(code)

    return codes
