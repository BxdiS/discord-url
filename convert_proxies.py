#!/usr/bin/env python3
"""Конвертер прокси из файла в формат config.toml"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dcurl.proxy_parse import parse_proxy_file


def main(proxy_file_path: str) -> None:
    """Конвертирует файл прокси и выводит TOML секцию."""
    path = Path(proxy_file_path)
    if not path.exists():
        print(f"Ошибка: файл {path} не найден", file=sys.stderr)
        return 1

    content = path.read_text(encoding="utf-8")
    urls = parse_proxy_file(content)

    if not urls:
        print("Нет валидных прокси в файле", file=sys.stderr)
        return 1

    print("[proxy]")
    print(f"urls = [")
    for url in urls:
        print(f'    "{url}",')
    print("]")
    print(f"skip_free_hours = 12.0")
    print()
    print(f"# Загружено {len(urls)} прокси", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Использование: python {sys.argv[0]} <файл_прокси>", file=sys.stderr)
        print(f"Пример: python {sys.argv[0]} proxies.txt > proxy_config.toml", file=sys.stderr)
        sys.exit(1)
    sys.exit(main(sys.argv[1]) or 0)
