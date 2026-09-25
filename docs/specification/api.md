# Вызовы

Родитель: [`index.md`](index.md). Номера «п. N» — пункты спецификации.

```python
from tgbot_chronicle import BotApi, Button, Chat, History, Place, Press, Record, Screen, Text

HOME = Screen.column("Дел нет.", Button("Проверить статус", "check"))

chat = Chat(BotApi(token), chat_id, owner_id, "~/.local/state/bot/chat.json",
            home=lambda: HOME,                      # что показывает страница, которую двигает библиотека
            history=History.AFFAIRS,                # или CHRONICLE — вид истории продукта (п. 13)
            actions=lambda ref: Screen(...),        # блок действий записи ref (п. 14)
            busy_seconds=900, block_seconds=600)    # занятая страница (п. 8), таймаут блока

chat.show(screen, loud=False)                       # страница: правка на месте или переезд (п. 1–7)
chat.record(Record("22.09: день рождения — Алиса", affairs={"occ:alisa"}))   # запись + страница (п. 9)
chat.close_affairs({"occ:alisa"})                   # дела закрыты — кнопка снимается (п. 15)
chat.fold_days(lambda day, refs: Record(...))       # прошедшие дни — строкой на день (п. 16)
chat.remove(message_id)                             # убрать свою запись

def handler(event):                                 # Press(data, place, record) или Text(text)
    return Screen(...)                              # исход там, где нажали; None — ничего

chat.serve(handler)                                 # простой цикл: poll → handle → tick
# свой цикл:
for event in chat.poll():
    chat.handle(event, handler)                     # «Выполняется…» по таймеру (п. 20)
chat.tick()                                         # таймаут блока, отложенный переезд
```

| Имя | Что это |
|---|---|
| `Screen(text, buttons)` | экран: HTML Телеграма и ряды кнопок; `Screen.column(text, *buttons)` — по кнопке в ряд |
| `Button(label, data)` | кнопка; данные не начинаются с `~` и `#` — они заняты библиотекой |
| `Record(title, lines, group, loud, affairs)` | запись истории |
| `RecordRef` | запись, лежащая в чате: номер, заголовок, строки, день, дела |
| `Press(data, place, record)` | нажатие живой кнопки; `place` — `Place.PAGE` или `Place.BLOCK` |
| `Text(text, message_id)` | свободный текст владельца |
| `Texts` | слова библиотеки: «Действия», «Свернуть», «Вернуть», вердикты; заменяются целиком |
| `TelegramError` → `NetworkError`, `ApiError` | отказ: сеть — повтор, API — разбор запроса (п. 23) |
| `StateError` | испорченный файл состояния, с путём (п. 25) |
| `tgbot_chronicle.testing.FakeTelegram` | подменный Bot API для тестов бота: помнит порядок сообщений и отвергает то же, что Телеграм |
