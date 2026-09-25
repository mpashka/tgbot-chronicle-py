"""Интерфейс Телеграм-бота как страница и летопись. Контракт — `docs/specification/`."""
from .api import ApiError, BotApi, NetworkError, TelegramError
from .chat import Chat
from .screen import (Button, Event, History, Place, Press, Record, RecordRef, Screen, Text,
                     Texts, expandable, short)
from .state import StateError, StateFile

__all__ = ["ApiError", "BotApi", "Button", "Chat", "Event", "History", "NetworkError", "Place",
           "Press", "Record", "RecordRef", "Screen", "StateError", "StateFile", "TelegramError",
           "Text", "Texts", "expandable", "short"]
__version__ = "0.1.0"
