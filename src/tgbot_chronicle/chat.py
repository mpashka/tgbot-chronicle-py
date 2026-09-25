"""Чат бота: одна страница, всегда последняя, и история под ней.

Правила — `docs/specification/index.md`; номера «п. N» ниже — его пункты.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from typing import Callable

from .api import ApiError, BotApi, TelegramError, log
from .screen import (Button, Event, History, Place, Press, Record, RecordRef, Screen, Text,
                     Texts, short)
from .state import StateFile

CALLBACK_LIMIT = 64
MERGED_LINES = 12
KEEP_DAYS = 8
KEEP_CODES = 500
OPEN, FOLD, BACK = "~open", "~fold", "~back"

Handler = Callable[[Event], "Screen | None"]


class Chat:
    """Один чат с одним человеком.

    `home` — экран, который страница показывает, когда её двигает сама библиотека: переезд под
    запись, освободившаяся занятая страница, сворачивание дней. `actions` — содержимое блока
    действий записи; обязателен в истории с делами.
    """

    def __init__(self, api: BotApi, chat_id: int, owner_id: int, state: StateFile | str,
                 home: Callable[[], Screen], history: History = History.CHRONICLE,
                 actions: Callable[[RecordRef], Screen] | None = None,
                 busy_seconds: float = 0, block_seconds: float = 600,
                 quick_seconds: float = 0.3, texts: Texts = Texts(),
                 clock: Callable[[], float] = time.time) -> None:
        if history is History.AFFAIRS and actions is None:
            raise ValueError("история с делами без `actions`: блоку действий нечего показать")
        self.api = api
        self.chat_id = int(chat_id)
        self.owner_id = int(owner_id)
        self.store = state if isinstance(state, StateFile) else StateFile(state)
        self.home = home
        self.history = history
        self.actions = actions
        self.busy_seconds = busy_seconds
        self.block_seconds = block_seconds
        self.quick_seconds = quick_seconds
        self.texts = texts
        self.clock = clock
        self.state: dict = {}
        self._stale: list[int] = []

    # --- операция -------------------------------------------------------------------------

    @contextmanager
    def _operation(self):
        """Операция целиком под замком: прочитал — сходил в сеть — записал (п. 25).

        Прежние страницы убираются последними, когда новая уже записана (п. 5): при обратном
        порядке сбой отправки терял бы страницу совсем.
        """
        with self.store.locked():
            self._load()
            yield
            self._forget()
            self.store.save(self.state)
            stale, self._stale = self._stale, []
            for message_id in stale:
                self._remove(message_id, self.texts.retired_page)

    def _load(self) -> None:
        state = self.store.load()
        for key, empty in (("offset", 0), ("last_id", 0), ("page", {}), ("records", []),
                           ("block", {}), ("codes", {}), ("next_code", 0)):
            state.setdefault(key, empty)
        self.state = state
        self._stale = []

    def _forget(self) -> None:
        horizon = self._today().toordinal() - KEEP_DAYS
        self.state["records"] = [
            item for item in self.state["records"]
            if item["affairs"] or date.fromisoformat(item["day"]).toordinal() >= horizon]

    def _today(self) -> date:
        return datetime.fromtimestamp(self.clock()).date()

    # --- сообщения ------------------------------------------------------------------------

    def _send(self, text: str, keyboard: dict | None, loud: bool,
              reply_to: int | None = None) -> int:
        sent = self.api.call(
            "sendMessage", chat_id=self.chat_id, text=text, parse_mode="HTML",
            reply_markup=keyboard, disable_notification=None if loud else True,
            reply_parameters={"message_id": reply_to, "allow_sending_without_reply": True}
            if reply_to else None)
        self.state["last_id"] = sent["message_id"]
        return sent["message_id"]

    def _edit(self, message_id: int, text: str, keyboard: dict | None) -> None:
        try:
            self.api.call("editMessageText", chat_id=self.chat_id, message_id=message_id,
                          text=text, parse_mode="HTML", reply_markup=keyboard)
        except ApiError as exc:
            if not exc.not_modified:
                raise

    def _strip(self, message_id: int) -> None:
        try:
            self.api.call("editMessageReplyMarkup", chat_id=self.chat_id, message_id=message_id)
        except ApiError as exc:
            if not exc.not_modified:
                log.info("кнопки с сообщения %s не сняты: %s", message_id, exc.description)

    def _remove(self, message_id: int, instead: str) -> None:
        """Удалить своё сообщение, а старше 48 часов — переписать в пометку без кнопок."""
        try:
            self.api.call("deleteMessage", chat_id=self.chat_id, message_id=message_id)
        except ApiError:
            try:
                self._edit(message_id, instead, None)
            except TelegramError as exc:
                log.info("сообщение %s не убрано: %s", message_id, exc)

    # --- кнопки ---------------------------------------------------------------------------

    # @tag:tbc-press
    def _code(self, data: str) -> str:
        """Данные кнопки или короткий код на них: обрезать идентификатор нельзя (п. 18)."""
        if len(data.encode()) <= CALLBACK_LIMIT:
            return data
        codes = self.state["codes"]
        for code, known in codes.items():
            if known == data:
                return code
        code = f"#{self.state['next_code']}"
        self.state["next_code"] += 1
        codes[code] = data
        if len(codes) > KEEP_CODES:
            del codes[next(iter(codes))]
        return code

    def _keyboard(self, rows, *system: tuple[str, str]) -> dict | None:
        """Кнопки бота рядами и системные — последним рядом по одной."""
        keyboard = [[{"text": button.label, "callback_data": self._code(button.data)}
                     for button in row] for row in rows if row]
        keyboard += [[{"text": label, "callback_data": data}] for label, data in system]
        return {"inline_keyboard": keyboard} if keyboard else None

    def _record_keyboard(self, affairs) -> dict | None:
        return self._keyboard((), (self.texts.actions, OPEN)) if affairs else None

    # --- страница -------------------------------------------------------------------------

    # @tag:tbc-page
    def show(self, screen: Screen, loud: bool = False) -> int:
        """Показать экран на странице. Возвращает номер сообщения страницы."""
        with self._operation():
            self._show(screen, loud)
            return self.state["page"]["id"]

    def _show(self, screen: Screen, loud: bool, reply_to: int | None = None) -> None:
        """Страница правится на месте, только если она последняя и показ тихий (п. 2–4)."""
        page = self.state["page"]
        page["screen"] = _dump(screen)
        text, keyboard = self._drawn(screen)
        known = page.get("id")
        if known and known == self.state["last_id"] and not loud:
            try:
                self._edit(known, text, keyboard)
                return
            except ApiError as exc:
                if not exc.not_found:
                    raise
                known = None
        page["id"] = self._send(text, keyboard, loud, reply_to)
        page["buried"] = False
        if known:
            self._stale.append(known)

    def _drawn(self, screen: Screen) -> tuple[str, dict | None]:
        """Пока раскрыт блок действий, у страницы одна кнопка — «Вернуть» (п. 14)."""
        block = self.state["block"]
        if not block:
            return screen.text, self._keyboard(screen.buttons)
        notice = self.texts.block_notice.format(title=short(block["title"], 60))
        return f"{screen.text}\n\n{notice}", self._keyboard((), (self.texts.back, BACK))

    def _redraw_page(self) -> None:
        if self.state["page"].get("screen"):
            self._show(_load_screen(self.state["page"]["screen"]), False)

    def _busy(self) -> bool:
        return self.clock() - self.state["page"].get("touched", 0) < self.busy_seconds

    # --- история --------------------------------------------------------------------------

    # @tag:tbc-history
    def record(self, record: Record, screen: Screen | None = None) -> int:
        """Запись истории и сразу под ней страница — одна операция (п. 9).

        `screen` — что показать на странице под записью; без него — `home`. Занятая страница
        остаётся на месте и переезжает в `tick` (п. 8). Возвращает номер сообщения записи.
        """
        if record.affairs and self.history is History.CHRONICLE:
            raise ValueError(f"запись «{record.title}» с делами, а история — летопись: "
                             "у записей летописи кнопок не бывает")
        with self._operation():
            message_id = self._write(record)
            if self._busy():
                self.state["page"]["buried"] = True
            else:
                self._show(screen or self.home(), False, reply_to=message_id)
            return message_id

    def _write(self, record: Record) -> int:
        """Однородная запись дня сливается с предыдущей (п. 16)."""
        today = self._today().isoformat()
        records = self.state["records"]
        last = records[-1] if records else None
        lines = [short(line) for line in record.lines]
        same = bool(last and record.group and not record.affairs and not last["affairs"]
                    and last["group"] == record.group and last["day"] == today
                    and last["loud"] == record.loud and not last.get("folded"))
        if same and not record.loud:
            merged = [*last["lines"], *lines][-MERGED_LINES:]
            try:
                self._edit(last["id"], _join(_head(last), merged), None)
                last["lines"] = merged
                return last["id"]
            except TelegramError as exc:
                log.info("запись %s не дописана, заведу новую: %s", last["id"], exc)
                same = False
        count = last["count"] + 1 if same else 1
        item = {"day": today, "title": record.title, "lines": lines, "group": record.group,
                "loud": record.loud, "count": count, "affairs": sorted(record.affairs)}
        item["id"] = self._send(_join(_head(item), lines), self._record_keyboard(record.affairs),
                                record.loud)
        if same:
            records.remove(last)
            self._remove(last["id"], self.texts.merged_record)
        records.append(item)
        return item["id"]

    def remove(self, message_id: int) -> None:
        """Убрать свою запись истории, отжившую своё."""
        with self._operation():
            self.state["records"] = [item for item in self.state["records"]
                                     if item["id"] != message_id]
            if self.state["block"].get("id") == message_id:
                self.state["block"] = {}
                self._redraw_page()
            self._remove(message_id, self.texts.folded_record)

    def close_affairs(self, keys) -> None:
        """Дела закрыты: с записей, у которых дел не осталось, снимается кнопка (п. 15)."""
        keys = set(keys)
        with self._operation():
            for item in self.state["records"]:
                if not keys & set(item["affairs"]):
                    continue
                item["affairs"] = sorted(set(item["affairs"]) - keys)
                if item["affairs"]:
                    continue
                if self.state["block"].get("id") == item["id"]:
                    self.state["block"] = {}
                    self._redraw_page()
                self._edit(item["id"], _join(_head(item), item["lines"]), None)

    def fold_days(self, line: Callable[[date, list[RecordRef]], Record | None]) -> None:
        """Записи прошедших дней — в одну строку на день; строку собирает бот (п. 16).

        `line` возвращает `None`, если дню нечего оставить в истории.
        """
        today = self._today().isoformat()
        with self._operation():
            days: dict[str, list[dict]] = {}
            for item in self.state["records"]:
                if item["day"] < today and not item.get("folded"):
                    days.setdefault(item["day"], []).append(item)
            if not days:
                return
            if self._item(self.state["block"].get("id")) in (x for v in days.values() for x in v):
                self.state["block"] = {}
            for day, items in sorted(days.items()):
                folded = line(date.fromisoformat(day), [_ref(item) for item in items])
                for item in items:
                    self.state["records"].remove(item)
                    self._remove(item["id"], self.texts.folded_record)
                if folded is None:
                    continue
                if folded.affairs and self.history is History.CHRONICLE:
                    raise ValueError("строка дня с делами, а история — летопись")
                lines = [short(text) for text in folded.lines]
                item = {"day": day, "title": folded.title, "lines": lines, "group": folded.group,
                        "loud": False, "count": 1, "affairs": sorted(folded.affairs),
                        "folded": True}
                item["id"] = self._send(_join(folded.title, lines),
                                        self._record_keyboard(folded.affairs), False)
                self.state["records"].append(item)
            self._show(self.home(), False)

    # --- блок действий --------------------------------------------------------------------

    def _open(self, item: dict) -> None:
        """Раскрыть блок в записи; прежний раскрытый сворачивается — блок в чате один (п. 14)."""
        if self.state["block"] and self.state["block"]["id"] != item["id"]:
            self._fold_block()
        self.state["block"] = {"id": item["id"], "title": item["title"], "at": self.clock()}
        self._draw_block(item, self.actions(_ref(item)))
        self._redraw_page()

    def _draw_block(self, item: dict, content: Screen) -> None:
        self._edit(item["id"], f"{_join(_head(item), item['lines'])}\n\n{content.text}",
                   self._keyboard(content.buttons, (self.texts.fold, FOLD)))

    def _fold_block(self) -> None:
        block, self.state["block"] = self.state["block"], {}
        item = self._item(block.get("id"))
        if item is None:
            return
        try:
            self._edit(item["id"], _join(_head(item), item["lines"]),
                       self._record_keyboard(item["affairs"]))
        except TelegramError as exc:
            log.info("блок %s не свёрнут: %s", item["id"], exc)

    def _collapse(self) -> None:
        """«Свернуть» в блоке и «Вернуть» на странице — одно действие (п. 14)."""
        self._fold_block()
        self._redraw_page()

    def _item(self, message_id) -> dict | None:
        return next((item for item in self.state["records"] if item["id"] == message_id), None)

    # --- время ----------------------------------------------------------------------------

    def tick(self) -> None:
        """Отложенное по времени: свернуть забытый блок, передвинуть освободившуюся страницу.
        Зовётся в цикле бота."""
        with self._operation():
            block = self.state["block"]
            if block and self.clock() - block["at"] >= self.block_seconds:
                self._collapse()
            if self.state["page"].get("buried") and not self._busy():
                self._show(self.home(), False)

    # --- ответы ---------------------------------------------------------------------------

    # @tag:tbc-press
    def poll(self, wait: int = 0) -> list[Event]:
        """Нажатия живых кнопок и тексты владельца с прошлого раза.

        На устаревшее нажатие библиотека отвечает вердиктом сама, и до бота оно не доходит
        (п. 19). Чужие обновления отбрасываются молча (п. 21).
        """
        with self._operation():
            updates = self.api.call("getUpdates", offset=self.state["offset"] or None,
                                    timeout=wait, allowed_updates=["message", "callback_query"])
            events: list[Event] = []
            for update in updates:
                self.state["offset"] = update["update_id"] + 1
                query, message = update.get("callback_query"), update.get("message")
                source = (query or message or {}).get("from", {})
                if source.get("id") != self.owner_id:
                    continue
                if query:
                    press = self._route(query)
                    if press is not None:
                        events.append(press)
                elif message.get("chat", {}).get("id") == self.chat_id:
                    self.state["last_id"] = max(self.state["last_id"], message["message_id"])
                    if message.get("text"):
                        events.append(Text(message["text"], message["message_id"]))
            return events

    def _route(self, query: dict) -> Press | None:
        message_id = query.get("message", {}).get("message_id")
        raw = query.get("data", "")
        data = self.state["codes"].get(raw, raw)
        page, block = self.state["page"], self.state["block"]
        if message_id == page.get("id"):
            if block and data == BACK:
                self._collapse()
                return self._answer(query["id"])
            if not block and not data.startswith("~"):
                page["touched"] = self.clock()
                return Press(data, Place.PAGE, query_id=query["id"])
            return self._answer(query["id"], self.texts.stale_page)
        item = self._item(message_id)
        if item is None or not item["affairs"]:
            self._strip(message_id)
            verdict = self.texts.stale_page if item is None else self.texts.stale_record
            return self._answer(query["id"], verdict)
        if data == OPEN:
            self._open(item)
            return self._answer(query["id"])
        if block.get("id") != message_id:
            self._edit(item["id"], _join(_head(item), item["lines"]),
                       self._record_keyboard(item["affairs"]))
            return self._answer(query["id"],
                                self.texts.stale_block.format(actions=self.texts.actions))
        if data == FOLD:
            self._collapse()
            return self._answer(query["id"])
        block["at"] = self.clock()
        return Press(data, Place.BLOCK, _ref(item), query["id"])

    def _answer(self, query_id: str, text: str | None = None) -> None:
        try:
            self.api.call("answerCallbackQuery", callback_query_id=query_id, text=text)
        except TelegramError as exc:
            log.info("на нажатие не ответить, оно устарело: %s", exc)

    def handle(self, event: Event, handler: Handler) -> None:
        """Выполнить нажатие или текст и показать исход там, где нажали (п. 20).

        На нажатие отвечают после нового экрана; если обработчик не уложился в
        `quick_seconds`, всплывает «Выполняется…». Ответить на нажатие можно один раз, поэтому
        после таймера исходом служит сам экран.
        """
        if isinstance(event, Text):
            screen = handler(event)
            if screen is not None:
                self.show(screen)
            return
        answered = threading.Event()

        def running() -> None:
            answered.set()
            self._answer(event.query_id, self.texts.running)

        timer = threading.Timer(self.quick_seconds, running)
        timer.start()
        try:
            screen = handler(event)
            if screen is not None:
                self._draw(event, screen)
        finally:
            timer.cancel()
            if not answered.is_set():
                answered.set()
                self._answer(event.query_id)

    def _draw(self, press: Press, screen: Screen) -> None:
        if press.place is Place.PAGE:
            self.show(screen)
            return
        with self._operation():
            item = self._item(press.record.message_id)
            if item is not None and self.state["block"].get("id") == item["id"]:
                self.state["block"]["at"] = self.clock()
                self._draw_block(item, screen)

    def serve(self, handler: Handler, once: bool = False, pause: float = 1,
              backoff: float = 300) -> None:
        """Цикл бота: опрос, обработка, отложенное. Бот со своим циклом зовёт `poll`,
        `handle` и `tick` сам."""
        wait = pause
        while True:
            try:
                for event in self.poll():
                    self.handle(event, handler)
                self.tick()
                wait = pause
            except TelegramError as exc:
                log.warning("%s", exc)
                wait = min(wait * 2, backoff)
            if once:
                return
            time.sleep(wait)


def _join(title: str, lines) -> str:
    return "\n".join([title, *lines])


def _head(item: dict) -> str:
    return f"{item['title']} ×{item['count']}" if item.get("count", 1) > 1 else item["title"]


def _ref(item: dict) -> RecordRef:
    return RecordRef(item["id"], item["title"], tuple(item["lines"]),
                     date.fromisoformat(item["day"]), frozenset(item["affairs"]))


def _dump(screen: Screen) -> dict:
    return {"text": screen.text,
            "buttons": [[[button.label, button.data] for button in row] for row in screen.buttons]}


def _load_screen(data: dict) -> Screen:
    return Screen(data["text"], tuple(tuple(Button(label, value) for label, value in row)
                                      for row in data["buttons"]))
