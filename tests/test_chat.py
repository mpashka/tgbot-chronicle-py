import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tgbot_chronicle import (Button, Chat, History, Place, Press, Record, Screen, StateError,
                             StateFile, Text)
from tgbot_chronicle.api import ApiError
from tgbot_chronicle.testing import FakeTelegram

DAY = 24 * 3600
HOME = Screen.column("Сводка", Button("Проверить", "check"))


class Case(unittest.TestCase):
    history = History.CHRONICLE

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.tg = FakeTelegram()
        self.tg.now = time.mktime((2026, 9, 25, 12, 0, 0, 0, 0, -1))
        self.presses: list = []
        self.chat = self.make()

    def tearDown(self) -> None:
        self.dir.cleanup()

    def make(self, **options) -> Chat:
        options.setdefault("history", self.history)
        if options["history"] is History.AFFAIRS:
            options.setdefault("actions", lambda ref: Screen.column(
                f"Дела: {', '.join(sorted(ref.affairs))}", Button("Написать", "write")))
        return Chat(self.tg, self.tg.chat_id, self.tg.owner_id,
                    str(Path(self.dir.name) / "chat.json"), home=lambda: HOME,
                    clock=lambda: self.tg.now, **options)

    def page(self):
        return self.tg.messages[self.chat.store.load()["page"]["id"]]

    def serve(self, handler=None) -> None:
        def record(event):
            self.presses.append(event)
            return handler(event) if handler else None

        for event in self.chat.poll():
            self.chat.handle(event, record)
        self.chat.tick()

    def assert_page_last(self) -> None:
        """Инвариант п. 1–2: страница одна, она последняя, кнопки есть только у неё и у
        записей с делами."""
        state = self.chat.store.load()
        records = {item["id"] for item in state.get("records", []) if item["affairs"]}
        self.assertEqual(self.tg.last().message_id, state["page"]["id"], self.tg.texts())
        others = [m.message_id for m in self.tg.with_buttons()
                  if m.message_id not in records | {state["page"]["id"]}]
        self.assertEqual(others, [], self.tg.texts())


class PageTest(Case):
    def test_quiet_show_on_the_last_page_edits_it(self):
        first = self.chat.show(HOME)
        second = self.chat.show(Screen.column("Экран 2", Button("Назад", "home")))
        self.assertEqual(first, second)
        self.assertEqual(len(self.tg.messages), 1)
        self.assertEqual(self.page().text, "Экран 2")

    def test_loud_show_sends_a_new_page_and_removes_the_old(self):
        first = self.chat.show(HOME)
        second = self.chat.show(HOME, loud=True)
        self.assertNotEqual(first, second)
        self.assertEqual(list(self.tg.messages), [second])
        self.assertTrue(self.tg.messages[second].loud)

    def test_page_under_a_human_message_moves_down(self):
        self.chat.show(HOME)
        self.tg.say("привет")
        self.serve()
        self.chat.show(HOME)
        self.assert_page_last()

    def test_page_deleted_by_human_comes_back(self):
        page = self.chat.show(HOME)
        del self.tg.messages[page]
        self.chat.show(HOME)
        self.assert_page_last()

    def test_old_page_that_cannot_be_deleted_says_where_the_page_went(self):
        old = self.chat.show(HOME)
        self.tg.now += 3 * DAY
        self.chat.record(Record("Событие"))
        self.assertEqual(self.tg.messages[old].text, "Страница переехала ниже.")
        self.assertEqual(self.tg.messages[old].buttons, [])
        self.assert_page_last()

    def test_the_old_page_is_removed_only_after_the_new_one_is_sent(self):
        old = self.chat.show(HOME)
        self.tg.fail["sendMessage"] = ApiError("sendMessage", 500, "boom")
        with self.assertRaises(ApiError):
            self.chat.record(Record("Событие"))
        self.assertIn(old, self.tg.messages)


class HistoryTest(Case):
    def test_record_and_page_move_is_one_operation(self):
        self.chat.show(HOME)
        note = self.chat.record(Record("Задача заведена", ("vpn-split",)))
        self.assert_page_last()
        self.assertEqual(self.page().reply_to, note)
        self.assertFalse(self.page().loud)

    def test_the_record_rings_not_the_page(self):
        self.chat.show(HOME)
        note = self.chat.record(Record("Сломалось", loud=True))
        self.assertTrue(self.tg.messages[note].loud)
        self.assertFalse(self.page().loud)

    def test_quiet_records_of_one_group_merge_into_one_message(self):
        first = self.chat.record(Record("Сторож", ("PR 1",), group="watch"))
        second = self.chat.record(Record("Сторож", ("PR 2",), group="watch"))
        self.assertEqual(first, second)
        self.assertEqual(self.tg.messages[first].text, "Сторож\nPR 1\nPR 2")
        self.assert_page_last()

    def test_loud_records_of_one_group_count_and_ring_again(self):
        first = self.chat.record(Record("Ждёт ответа", group="ask", loud=True))
        second = self.chat.record(Record("Ждёт ответа", group="ask", loud=True))
        self.assertNotIn(first, self.tg.messages)
        self.assertEqual(self.tg.messages[second].text, "Ждёт ответа ×2")
        self.assertTrue(self.tg.messages[second].loud)

    def test_records_of_another_day_do_not_merge(self):
        first = self.chat.record(Record("Сторож", group="watch"))
        self.tg.now += DAY
        second = self.chat.record(Record("Сторож", group="watch"))
        self.assertNotEqual(first, second)

    def test_chronicle_refuses_a_record_with_affairs(self):
        with self.assertRaises(ValueError):
            self.chat.record(Record("Повод", affairs=frozenset({"occ:1"})))

    def test_long_line_is_cut(self):
        note = self.chat.record(Record("Вывод", ("x" * 500,)))
        self.assertLess(len(self.tg.messages[note].text), 200)

    def test_remove_drops_the_record(self):
        note = self.chat.record(Record("Событие"))
        self.chat.remove(note)
        self.assertNotIn(note, self.tg.messages)
        self.assert_page_last()

    def test_past_days_fold_into_a_line_per_day(self):
        self.chat.record(Record("Отправлено: папа"))
        self.chat.record(Record("Отправлено: мама"))
        self.tg.now += DAY
        seen = []

        def line(day, refs):
            seen.append((day, [ref.title for ref in refs]))
            return Record(f"{day:%d.%m}: отправлено — {len(refs)}")

        self.chat.fold_days(line)
        self.assertEqual(seen, [(date(2026, 9, 25), ["Отправлено: папа", "Отправлено: мама"])])
        self.assertEqual(self.tg.texts(), ["25.09: отправлено — 2", "Сводка"])
        self.chat.fold_days(line)
        self.assertEqual(len(seen), 1)


class PressTest(Case):
    def test_press_on_the_page_reaches_the_bot_and_the_answer_comes_after(self):
        page = self.chat.show(HOME)
        self.tg.press(page, "Проверить")
        self.serve(lambda event: Screen.column("Проверено", Button("Назад", "home")))
        self.assertEqual(self.presses, [Press("check", Place.PAGE, query_id="q0")])
        self.assertEqual(self.page().text, "Проверено")
        self.assertEqual(self.tg.answers, [("q0", None)])

    def test_press_on_an_old_page_answers_with_a_verdict(self):
        old = self.chat.show(HOME)
        self.chat.show(HOME, loud=True)
        self.tg.messages[old] = type(self.tg.last())(old, "старая", [[{"text": "Проверить",
                                                                         "callback_data": "check"}]])
        self.tg.press(old, "Проверить")
        self.serve()
        self.assertEqual(self.presses, [])
        self.assertEqual(self.tg.answers[0][1], "Эта кнопка устарела — живая страница внизу чата.")
        self.assertEqual(self.tg.messages[old].buttons, [])

    def test_stranger_is_ignored_silently(self):
        page = self.chat.show(HOME)
        self.tg.press(page, "Проверить", sender=666)
        self.tg.say("я хозяин", sender=666)
        self.serve()
        self.assertEqual(self.presses, [])
        self.assertEqual(self.tg.answers, [])

    def test_text_is_data_for_the_bot(self):
        self.chat.show(HOME)
        said = self.tg.say("/start")
        self.serve(lambda event: HOME)
        self.assertEqual(self.presses, [Text("/start", said)])
        self.assert_page_last()

    def test_long_button_data_travels_by_a_short_code(self):
        data = "task|" + "vpn-split-" * 10
        page = self.chat.show(Screen.column("Экран", Button("Открыть", data)))
        self.tg.press(page, "Открыть")
        self.serve()
        self.assertEqual(self.presses[0].data, data)

    def test_slow_handler_shows_running_and_then_the_outcome(self):
        self.chat = self.make(quick_seconds=0.01)
        page = self.chat.show(HOME)
        self.tg.press(page, "Проверить")

        def slow(event):
            time.sleep(0.05)
            return Screen.column("Готово", Button("Назад", "home"))

        self.serve(slow)
        self.assertEqual(self.tg.answers, [("q0", "Выполняется…")])
        self.assertEqual(self.page().text, "Готово")

    def test_reserved_button_data_is_refused(self):
        with self.assertRaises(ValueError):
            Button("Системная", "~open")


class BusyPageTest(Case):
    def test_busy_page_stays_and_moves_when_free(self):
        self.chat = self.make(busy_seconds=900)
        page = self.chat.show(HOME)
        self.tg.press(page, "Проверить")
        self.serve(lambda event: Screen.column("Черновик", Button("Назад", "home")))
        self.chat.record(Record("Канал WhatsApp не отвечает"))
        self.assertEqual(self.page().message_id, page)
        self.assertEqual(self.page().text, "Черновик")
        self.tg.now += 1000
        self.chat.tick()
        self.assert_page_last()
        self.assertEqual(self.page().text, "Сводка")


class AffairsTest(Case):
    history = History.AFFAIRS

    def open_block(self):
        self.chat.show(HOME)
        note = self.chat.record(Record("22.09: день рождения — Алиса",
                                       affairs=frozenset({"occ:alisa"})))
        self.tg.press(note, "Действия")
        self.serve()
        return note

    def test_affairs_history_needs_actions(self):
        with self.assertRaises(ValueError):
            Chat(self.tg, 1, 7, "x.json", home=lambda: HOME, history=History.AFFAIRS)

    def test_record_with_affairs_has_one_button(self):
        self.chat.show(HOME)
        note = self.chat.record(Record("Повод", affairs=frozenset({"occ:1"})))
        self.assertEqual(self.tg.buttons(note), ["Действия"])
        self.assert_page_last()

    def test_actions_open_in_the_record_and_the_page_offers_return(self):
        note = self.open_block()
        self.assertEqual(self.tg.buttons(note), ["Написать", "Свернуть"])
        self.assertIn("Дела: occ:alisa", self.tg.messages[note].text)
        self.assertEqual(self.tg.buttons(self.page().message_id), ["Вернуть"])
        self.assertIn("▲ Действия открыты выше", self.page().text)
        self.assertEqual(self.presses, [])

    def test_press_in_the_block_reaches_the_bot_and_draws_in_the_block(self):
        note = self.open_block()
        self.tg.press(note, "Написать")
        self.serve(lambda event: Screen.column("Черновик Алисе", Button("Отправить", "send")))
        press = self.presses[0]
        self.assertEqual((press.data, press.place, press.record.affairs),
                         ("write", Place.BLOCK, frozenset({"occ:alisa"})))
        self.assertEqual(self.tg.buttons(note), ["Отправить", "Свернуть"])

    def test_return_on_the_page_folds_the_block(self):
        note = self.open_block()
        self.tg.press(self.page().message_id, "Вернуть")
        self.serve()
        self.assertEqual(self.tg.buttons(note), ["Действия"])
        self.assertEqual(self.page().text, "Сводка")
        self.assertEqual(self.tg.buttons(self.page().message_id), ["Проверить"])

    def test_only_one_block_is_open(self):
        first = self.open_block()
        second = self.chat.record(Record("Пора написать: Марина", affairs=frozenset({"due:m"})))
        self.tg.press(second, "Действия")
        self.serve()
        self.assertEqual(self.tg.buttons(first), ["Действия"])
        self.assertEqual(self.tg.buttons(second), ["Написать", "Свернуть"])

    def test_block_folds_itself_after_timeout(self):
        note = self.open_block()
        self.tg.now += 601
        self.chat.tick()
        self.assertEqual(self.tg.buttons(note), ["Действия"])
        self.assertEqual(self.tg.buttons(self.page().message_id), ["Проверить"])

    def test_press_in_a_folded_block_answers_with_a_verdict(self):
        note = self.open_block()
        self.tg.now += 601
        self.chat.tick()
        self.tg.messages[note].buttons = [[{"text": "Написать", "callback_data": "write"}]]
        self.tg.press(note, "Написать")
        self.serve()
        self.assertEqual(self.presses, [])
        self.assertIn("уже свёрнуты", self.tg.answers[-1][1])
        self.assertEqual(self.tg.buttons(note), ["Действия"])

    def test_closed_affairs_remove_the_button(self):
        note = self.open_block()
        self.chat.close_affairs({"occ:alisa"})
        self.assertEqual(self.tg.buttons(note), [])
        self.assertEqual(self.tg.buttons(self.page().message_id), ["Проверить"])
        self.tg.messages[note].buttons = [[{"text": "Действия", "callback_data": "~open"}]]
        self.tg.press(note, "Действия")
        self.serve()
        self.assertEqual(self.tg.answers[-1][1], "Дела по этому событию уже закрыты — кнопка снята.")

    def test_folded_day_keeps_the_button_while_affairs_are_open(self):
        self.chat.record(Record("Повод", affairs=frozenset({"occ:1"})))
        self.tg.now += DAY
        self.chat.fold_days(lambda day, refs: Record(
            f"{day:%d.%m}: повод", affairs=frozenset().union(*(r.affairs for r in refs))))
        folded = self.tg.order()[0]
        self.assertEqual(self.tg.buttons(folded.message_id), ["Действия"])
        self.assert_page_last()


class StateTest(Case):
    def test_broken_state_is_named_not_ignored(self):
        path = Path(self.dir.name) / "broken.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(StateError) as caught:
            StateFile(path).load()
        self.assertIn(str(path), str(caught.exception))

    def test_state_survives_a_new_chat_object(self):
        page = self.chat.show(HOME)
        self.make().show(Screen.column("Другой", Button("Назад", "home")))
        self.assertEqual(self.tg.messages[page].text, "Другой")
        self.assertEqual(len(self.tg.messages), 1)


if __name__ == "__main__":
    unittest.main()
