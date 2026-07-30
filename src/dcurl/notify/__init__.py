"""Каналы уведомлений."""

from .telegram import TelegramError, TelegramNotifier

__all__ = ["TelegramNotifier", "TelegramError"]
