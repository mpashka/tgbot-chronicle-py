import http.client
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tgbot_chronicle.api import ApiError, BotApi, NetworkError, _Connection


def _answer(body: bytes, status: int = 200):
    return mock.Mock(status=status, read=mock.Mock(return_value=body))


class BotApiTest(unittest.TestCase):
    def setUp(self):
        self.api = BotApi("t")

    def test_read_timeout_is_a_network_refusal_named_by_method(self):
        with mock.patch.object(_Connection, "request", side_effect=TimeoutError("read timed out")):
            with self.assertRaises(NetworkError) as caught:
                self.api.call("getMe")
        self.assertIn("getMe", str(caught.exception))

    def test_connection_closed_by_server_is_reopened_once(self):
        idle = mock.Mock()
        idle.getresponse.side_effect = http.client.RemoteDisconnected("idle")
        self.api._local.connection = idle
        with mock.patch.object(_Connection, "request"), \
                mock.patch.object(_Connection, "getresponse", return_value=_answer(
                    b'{"ok": true, "result": {"username": "bot"}}')):
            self.assertEqual(self.api.call("getMe"), {"username": "bot"})
        idle.close.assert_called_once()

    def test_fresh_connection_refusal_is_not_repeated(self):
        with mock.patch.object(_Connection, "request") as request, \
                mock.patch.object(_Connection, "getresponse",
                                  side_effect=ConnectionResetError("reset")):
            with self.assertRaises(NetworkError):
                self.api.call("sendMessage", text="x")
        request.assert_called_once()

    def test_api_refusal_keeps_status_and_description(self):
        with mock.patch.object(_Connection, "request"), \
                mock.patch.object(_Connection, "getresponse",
                                  return_value=_answer(b'{"ok": false, "description": "Conflict"}',
                                                       409)):
            with self.assertRaises(ApiError) as caught:
                self.api.call("getUpdates")
        self.assertEqual((caught.exception.status, caught.exception.description), (409, "Conflict"))

    def test_none_fields_are_not_sent(self):
        with mock.patch.object(_Connection, "request") as request, \
                mock.patch.object(_Connection, "getresponse",
                                  return_value=_answer(b'{"ok": true, "result": true}')):
            self.api.call("editMessageText", text="x", reply_markup=None)
        self.assertNotIn(b"reply_markup", request.call_args.kwargs["body"])

    def test_network_failure_is_logged_once_per_reason(self):
        with mock.patch.object(_Connection, "request", side_effect=TimeoutError("t")), \
                self.assertLogs("tgbot_chronicle", "WARNING") as logs:
            for _ in range(3):
                with self.assertRaises(NetworkError):
                    self.api.call("getUpdates")
        self.assertEqual(len(logs.records), 1)


if __name__ == "__main__":
    unittest.main()
