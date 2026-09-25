"""Подменный Bot API для тестов библиотеки и ботов на ней.

Помнит порядок сообщений чата и отвергает то же, что настоящий Телеграм: `reply_markup: null`,
правку несуществующего сообщения, удаление старше 48 часов, данные кнопки длиннее 64 байт.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .api import ApiError

DELETE_LIMIT = 48 * 3600


@dataclass
class Message:
    message_id: int
    text: str
    buttons: list = field(default_factory=list)
    mine: bool = True
    loud: bool = False
    reply_to: int | None = None
    at: float = 0


class FakeTelegram:
    """Вместо `BotApi`. `now` двигает тест; `calls` — все вызовы по порядку."""

    def __init__(self, chat_id: int = 1, owner_id: int = 7) -> None:
        self.chat_id = chat_id
        self.owner_id = owner_id
        self.now = 0.0
        self.messages: dict[int, Message] = {}
        self.calls: list[tuple[str, dict]] = []
        self.answers: list[tuple[str, str | None]] = []
        self.updates: list[dict] = []
        self.fail: dict[str, Exception] = {}
        self._next_id = 100
        self._next_update = 1

    # --- что видит человек ----------------------------------------------------------------

    def order(self) -> list[Message]:
        return [self.messages[key] for key in sorted(self.messages)]

    def last(self) -> Message:
        return self.order()[-1]

    def with_buttons(self) -> list[Message]:
        return [message for message in self.order() if message.mine and message.buttons]

    def texts(self) -> list[str]:
        return [message.text for message in self.order()]

    # --- что делает человек ---------------------------------------------------------------

    def press(self, message_id: int, label: str, sender: int | None = None) -> None:
        message = self.messages[message_id]
        data = next(button["callback_data"] for row in message.buttons for button in row
                    if button["text"] == label)
        self.press_data(message_id, data, sender)

    def press_data(self, message_id: int, data: str, sender: int | None = None) -> None:
        self.updates.append({"update_id": self._update(), "callback_query": {
            "id": f"q{len(self.updates)}", "data": data,
            "from": {"id": self.owner_id if sender is None else sender},
            "message": {"message_id": message_id, "chat": {"id": self.chat_id}}}})

    def say(self, text: str, sender: int | None = None) -> int:
        message_id = self._id()
        self.messages[message_id] = Message(message_id, text, mine=False, at=self.now)
        self.updates.append({"update_id": self._update(), "message": {
            "message_id": message_id, "text": text, "chat": {"id": self.chat_id},
            "from": {"id": self.owner_id if sender is None else sender}}})
        return message_id

    def buttons(self, message_id: int) -> list[str]:
        return [button["text"] for row in self.messages[message_id].buttons for button in row]

    # --- Bot API --------------------------------------------------------------------------

    def call(self, method: str, **payload):
        payload = {key: value for key, value in payload.items() if value is not None}
        json.dumps(payload)
        self.calls.append((method, payload))
        if method in self.fail:
            raise self.fail.pop(method)
        markup = payload.get("reply_markup", {})
        if markup is None:
            raise ApiError(method, 400, "Bad Request: object expected as reply markup")
        for row in (markup or {}).get("inline_keyboard", []):
            for button in row:
                if len(button["callback_data"].encode()) > 64:
                    raise ApiError(method, 400, "Bad Request: BUTTON_DATA_INVALID")
        return getattr(self, "_" + method)(**payload)

    def _sendMessage(self, chat_id, text, reply_markup=None, disable_notification=False,
                     reply_parameters=None, **_):
        message_id = self._id()
        self.messages[message_id] = Message(
            message_id, text, (reply_markup or {}).get("inline_keyboard", []),
            loud=not disable_notification, at=self.now,
            reply_to=(reply_parameters or {}).get("message_id"))
        return {"message_id": message_id}

    def _editMessageText(self, chat_id, message_id, text, reply_markup=None, **_):
        message = self._mine(message_id, "editMessageText")
        buttons = (reply_markup or {}).get("inline_keyboard", [])
        if message.text == text and message.buttons == buttons:
            raise ApiError("editMessageText", 400, "Bad Request: message is not modified")
        message.text, message.buttons = text, buttons
        return True

    def _editMessageReplyMarkup(self, chat_id, message_id, reply_markup=None, **_):
        message = self._mine(message_id, "editMessageReplyMarkup")
        buttons = (reply_markup or {}).get("inline_keyboard", [])
        if message.buttons == buttons:
            raise ApiError("editMessageReplyMarkup", 400, "Bad Request: message is not modified")
        message.buttons = buttons
        return True

    def _deleteMessage(self, chat_id, message_id, **_):
        message = self._mine(message_id, "deleteMessage")
        if self.now - message.at > DELETE_LIMIT:
            raise ApiError("deleteMessage", 400,
                           "Bad Request: message can't be deleted for everyone")
        del self.messages[message_id]
        return True

    def _answerCallbackQuery(self, callback_query_id, text=None, **_):
        self.answers.append((callback_query_id, text))
        return True

    def _getUpdates(self, offset=None, **_):
        pending = [update for update in self.updates if update["update_id"] >= (offset or 0)]
        self.updates = []
        return pending

    def _mine(self, message_id: int, method: str) -> Message:
        message = self.messages.get(message_id)
        if message is None or not message.mine:
            raise ApiError(method, 400, "Bad Request: message to edit not found")
        return message

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _update(self) -> int:
        self._next_update += 1
        return self._next_update
