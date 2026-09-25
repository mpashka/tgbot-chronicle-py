"""Что бот отдаёт библиотеке и что получает от неё: экран, кнопка, запись, событие."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

LINE_LIMIT = 160
RESERVED = ("~", "#")


class History(Enum):
    """Вид истории продукта (слой `chat`, правило 5): решает, бывают ли у записи кнопки."""

    CHRONICLE = "chronicle"
    AFFAIRS = "affairs"


@dataclass(frozen=True)
class Button:
    label: str
    data: str

    def __post_init__(self) -> None:
        if self.data.startswith(RESERVED):
            raise ValueError(f"кнопка {self.label!r}: данные не могут начинаться с "
                             f"{' или '.join(RESERVED)} — эти знаки заняты библиотекой")


@dataclass(frozen=True)
class Screen:
    """Экран: текст в HTML Телеграма и ряды кнопок."""

    text: str
    buttons: tuple[tuple[Button, ...], ...] = ()

    @classmethod
    def column(cls, text: str, *buttons: Button) -> Screen:
        return cls(text, tuple((button,) for button in buttons))


@dataclass(frozen=True)
class Record:
    """Запись истории. Строки — HTML; слишком длинная строка обрезается (правило 4).

    `group` — однородность: запись той же группы, что предыдущая, сливается с ней. Пустая группа
    не сливается ни с чем. `affairs` — ключи открытых дел; бывают только в истории с делами.
    """

    title: str
    lines: tuple[str, ...] = ()
    group: str = ""
    loud: bool = False
    affairs: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RecordRef:
    """Запись, уже лежащая в чате, — такой её видит бот в нажатии и при сворачивании дней."""

    message_id: int
    title: str
    lines: tuple[str, ...]
    day: date
    affairs: frozenset[str]


class Place(Enum):
    PAGE = "page"
    BLOCK = "block"


@dataclass(frozen=True)
class Press:
    """Нажатие живой кнопки бота. Устаревшие сюда не попадают — на них ответила библиотека."""

    data: str
    place: Place
    record: RecordRef | None = None
    query_id: str = ""


@dataclass(frozen=True)
class Text:
    """Свободный текст человека. Данные, а не команда: команду бот опознаёт сам."""

    text: str
    message_id: int


Event = Press | Text


@dataclass(frozen=True)
class Texts:
    """Слова, которые библиотека пишет сама. Бот может заменить любое."""

    actions: str = "Действия"
    fold: str = "Свернуть"
    back: str = "Вернуть"
    block_notice: str = "▲ Действия открыты выше: «{title}»."
    retired_page: str = "Страница переехала ниже."
    folded_record: str = "Свёрнуто в строку дня."
    merged_record: str = "Повторилось ниже."
    running: str = "Выполняется…"
    stale_page: str = "Эта кнопка устарела — живая страница внизу чата."
    stale_block: str = ("Действия этого сообщения уже свёрнуты — открой их заново кнопкой "
                        "«{actions}».")
    stale_record: str = "Дела по этому событию уже закрыты — кнопка снята."


_TAG = re.compile(r"<[^>]+>")


def short(line: str, limit: int = LINE_LIMIT) -> str:
    """Строка записи не длиннее предела. Обрезанная теряет разметку: резать теги нельзя."""
    visible = html.unescape(_TAG.sub("", line))
    if len(visible) <= limit:
        return line
    return html.escape(visible[:limit].rstrip(), quote=False) + "…"


def expandable(body: str) -> str:
    """Свёрнутая цитата: текст, который человек подтверждает, целиком, но в одну строку."""
    return f"<blockquote expandable>{html.escape(body.strip(), quote=False)}</blockquote>"
