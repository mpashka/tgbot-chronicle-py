"""Bot API Телеграма: вызов метода и отказ, из которого понятно, что лечить."""
from __future__ import annotations

import http.client
import json
import logging
import threading

log = logging.getLogger("tgbot_chronicle")

HOST = "api.telegram.org"
# Маршрут до api.telegram.org перекладывается, когда поднимается VPN, и открытое соединение
# виснет: ждать ответа дольше десяти секунд бессмысленно.
READ_TIMEOUT = 10
# Каждое десятое TCP-соединение по IPv4 зависает, а Python ждёт первый адрес целиком, прежде
# чем пробовать следующий: короткий таймаут относится к каждому адресу отдельно.
CONNECT_TIMEOUT = 2


class TelegramError(Exception):
    def __init__(self, method: str, reason: str) -> None:
        super().__init__(f"telegram {method}: {reason}")
        self.method = method
        self.reason = reason


class NetworkError(TelegramError):
    """Запрос мог не дойти: лечится повтором."""


class ApiError(TelegramError):
    """Телеграм запрос отверг: лечится разбором запроса, повтор не поможет."""

    def __init__(self, method: str, status: int, description: str) -> None:
        super().__init__(method, f"{status} {description}")
        self.status = status
        self.description = description

    @property
    def not_modified(self) -> bool:
        return "message is not modified" in self.description

    @property
    def not_found(self) -> bool:
        return "not found" in self.description


class _Connection(http.client.HTTPSConnection):
    def connect(self) -> None:
        self.timeout = CONNECT_TIMEOUT
        super().connect()
        self.sock.settimeout(READ_TIMEOUT)
        self.timeout = READ_TIMEOUT


class BotApi:
    """Вызовы по постоянному соединению — своему у каждого потока.

    Поле со значением `None` в запрос не попадает: Телеграм отвергает `reply_markup: null`, а
    правка без поля и есть снятие кнопок.
    """

    def __init__(self, token: str, host: str = HOST) -> None:
        self._token = token
        self._host = host
        self._local = threading.local()
        self._failure = ""

    def call(self, method: str, **payload) -> dict | list | bool:
        body = json.dumps({k: v for k, v in payload.items() if v is not None},
                          ensure_ascii=False).encode()
        try:
            status, raw = self._post(method, body)
        except NetworkError as exc:
            self._failed(exc.reason)
            raise
        self._recovered()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError(method, status, raw.decode("utf-8", "replace")[:200]) from None
        if status != 200 or not result.get("ok"):
            raise ApiError(method, status, str(result.get("description", ""))[:300])
        return result["result"]

    def _post(self, method: str, body: bytes) -> tuple[int, bytes]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        path = f"/bot{self._token}/{method}"
        for reused in (True, False):
            connection = getattr(self._local, "connection", None)
            if connection is None:
                connection, reused = _Connection(self._host, timeout=READ_TIMEOUT), False
                self._local.connection = connection
            try:
                connection.request("POST", path, body=body, headers=headers)
                response = connection.getresponse()
                return response.status, response.read()
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError) as exc:
                # Сервер закрыл простаивавшее соединение: запрос не дошёл, повтор не задвоит.
                self._drop()
                if not reused:
                    raise NetworkError(method, f"соединение оборвано ({exc})") from exc
            except (OSError, http.client.HTTPException) as exc:
                # Таймаут не повторяется: запрос мог и дойти.
                self._drop()
                raise NetworkError(method, f"сеть недоступна ({exc})") from exc
        raise NetworkError(method, "соединение не установлено")

    def _drop(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
        self._local.connection = None

    def _failed(self, reason: str) -> None:
        kind = reason.split(" (", 1)[0]
        if kind != self._failure:
            log.warning("Telegram не отвечает: %s", reason)
            self._failure = kind

    def _recovered(self) -> None:
        if self._failure:
            log.warning("Telegram снова отвечает")
            self._failure = ""
