#!/usr/bin/env python3
"""Запуск без установки пакета: python run.py check discord.gg/discord"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dcurl.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
