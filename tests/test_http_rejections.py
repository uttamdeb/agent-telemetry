"""Rejected POST bodies are discarded within strict size and time bounds."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D


class HTTPRejectionTests(unittest.TestCase):
    def handler(self, headers):
        handler = object.__new__(D.Handler)
        handler.headers = headers
        handler.connection = Mock()
        handler.connection.gettimeout.return_value = None
        handler.rfile = Mock()
        return handler

    def test_small_rejected_body_is_consumed_before_403_without_parsing_or_actions(self):
        body = b'{"confirmed":true}'
        handler = self.handler({"Content-Type": "text/plain", "Content-Length": str(len(body))})
        handler.path = "/api/local-servers/start"
        handler.client_address = ("127.0.0.1", 12345)
        events = []
        handler.rfile.read1.side_effect = lambda length: events.append(("read", length)) or body
        handler._send = Mock(side_effect=lambda status, payload: events.append(("response", status)))
        with patch.object(D, "LocalServerInventory") as inventory, patch.object(D.json, "loads") as parse:
            handler.do_POST()
        self.assertEqual(events, [("read", len(body)), ("response", 403)])
        inventory.assert_not_called()
        parse.assert_not_called()
        self.assertIsNone(handler.connection.settimeout.call_args.args[0])

    def test_unknown_oversized_and_transfer_encoded_bodies_are_never_read(self):
        cases = (
            {}, {"Content-Length": "invalid"}, {"Content-Length": "-1"},
            {"Content-Length": "16385"},
            {"Content-Length": "3", "Transfer-Encoding": "chunked"},
        )
        for headers in cases:
            with self.subTest(headers=headers):
                handler = self.handler(headers)
                handler._discard_small_post_body()
                handler.rfile.read1.assert_not_called()
                handler.connection.settimeout.assert_not_called()

    def test_slow_drip_cannot_extend_the_total_discard_deadline(self):
        handler = self.handler({"Content-Length": "3"})
        handler.rfile.read1.return_value = b"x"
        # Two short reads consume the full deadline; a third read cannot reset it.
        with patch.object(D.time, "monotonic", side_effect=[100, 100, 100.1, 100.21]):
            handler._discard_small_post_body()
        self.assertEqual([call.args[0] for call in handler.rfile.read1.call_args_list], [3, 2])
        timeouts = [call.args[0] for call in handler.connection.settimeout.call_args_list]
        self.assertAlmostEqual(timeouts[0], 0.2)
        self.assertAlmostEqual(timeouts[1], 0.1)
        self.assertIsNone(timeouts[2])

    def test_read_timeout_and_eof_restore_the_previous_socket_timeout(self):
        for outcome in (TimeoutError("mock deadline"), b""):
            with self.subTest(outcome=type(outcome).__name__):
                handler = self.handler({"Content-Length": "16384"})
                handler.connection.gettimeout.return_value = 5
                if isinstance(outcome, Exception):
                    handler.rfile.read1.side_effect = outcome
                else:
                    handler.rfile.read1.return_value = outcome
                handler._discard_small_post_body()
                handler.rfile.read1.assert_called_once_with(16384)
                self.assertEqual(handler.connection.settimeout.call_args.args[0], 5)


if __name__ == "__main__":
    unittest.main()
