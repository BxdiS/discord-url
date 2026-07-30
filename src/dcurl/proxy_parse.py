"""Парсинг прокси в разных форматах, с поддержкой диапазонов портов."""

from __future__ import annotations


def expand_port_range(template: str) -> list[str]:
    """Расширяет диапазон портов. Поддерживает два формата:

    Формат 1 (рекомендуется):
    - user:pass@host:10000-10999
    -> ['http://user:pass@host:10000', ..., '@host:10999']

    Формат 2:
    - host:10000-10999:user:pass
    -> ['http://user:pass@host:10000', ..., '@host:10999']
    """
    template = template.strip()

    # Формат 1: user:pass@host:start-end
    if "@" in template:
        try:
            auth, host_port = template.rsplit("@", 1)
            host, port_range = host_port.rsplit(":", 1)
            if "-" in port_range:
                start, end = port_range.split("-", 1)
                start_port = int(start)
                end_port = int(end)
                urls = []
                for port in range(start_port, end_port + 1):
                    url = f"http://{auth}@{host}:{port}"
                    urls.append(url)
                return urls
        except (ValueError, AttributeError):
            pass

    # Формат 2: host:start-end:user:pass
    parts = template.split(":")
    if len(parts) == 4:
        host, port_range, user, passwd = parts
        if "-" in port_range:
            try:
                start, end = port_range.split("-", 1)
                start_port = int(start)
                end_port = int(end)
                urls = []
                for port in range(start_port, end_port + 1):
                    url = f"http://{user}:{passwd}@{host}:{port}"
                    urls.append(url)
                return urls
            except (ValueError, IndexError):
                pass

    return []


def parse_proxy(line: str) -> str | None:
    """Преобразует прокси в стандартный URL вид.

    Поддерживает форматы:
    - http://user:pass@host:port
    - host:port:user:pass
    - host:port (без авторизации)
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # Уже в URL-виде
    if "://" in line:
        return line

    # Формат host:port:user:pass
    parts = line.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        return f"http://{user}:{passwd}@{host}:{port}"

    # Формат host:port
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"

    return None


def parse_proxy_file(content: str) -> list[str]:
    """Разбирает файл прокси и возвращает список URL.

    Поддерживает:
    - Обычные прокси: host:port:user:pass, http://user:pass@host:port, и т.д.
    - Диапазоны портов: host:10000-10999:user:pass
    """
    urls = []
    seen = set()

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Проверить, это диапазон портов
        if ":" in line and "-" in line:
            parts = line.split(":")
            if len(parts) == 4 and "-" in parts[1]:
                # Похоже на диапазон портов: host:start-end:user:pass
                expanded = expand_port_range(line)
                for url in expanded:
                    if url not in seen:
                        seen.add(url)
                        urls.append(url)
                continue

        # Обычный прокси
        url = parse_proxy(line)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    return urls
