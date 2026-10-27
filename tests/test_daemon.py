import base64
import http.client
import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_daemon as d  # noqa: E402


class DaemonRequests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), d.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_say_accepted(self):
        status, _ = self.req("POST", "/say", b'{"text": "hello"}')
        self.assertEqual(status, 200)

    def test_web_page_post_refused(self):
        # What a web page can send without a CORS preflight.
        status, _ = self.req("POST", "/say", b'{"text": "hi"}',
                             {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_cross_origin_refused(self):
        status, _ = self.req("POST", "/say", b'{"text": "hi"}',
                             {"Origin": "https://example.com"})
        self.assertEqual(status, 403)

    def test_foreign_host_refused(self):
        # DNS rebinding: the browser sends the attacker's name as Host.
        status, _ = self.req("GET", "/status", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_localhost_host_accepted(self):
        status, _ = self.req("GET", "/health",
                             headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(status, 200)

    def test_json_list_is_a_400_not_a_dropped_connection(self):
        self.assertEqual(self.req("POST", "/say", b"[1, 2]")[0], 400)
        self.assertEqual(self.req("POST", "/transcribe", b"[1, 2]")[0], 400)

    def test_bad_speed(self):
        status, _ = self.req("POST", "/say", b'{"text": "x", "speed": "fast"}')
        self.assertEqual(status, 400)

    def test_transcribe_format_cannot_be_a_path(self):
        body = json.dumps({"audio_b64": base64.b64encode(b"x").decode(),
                           "format": ".\\..\\..\\escape"}).encode()
        status, _ = self.req("POST", "/transcribe", body)
        self.assertEqual(status, 400)

    def test_skip_stops_playback(self):
        with mock.patch.object(d.ps, "stop_playback") as stop:
            status, _ = self.req("POST", "/skip", b"{}")
        self.assertEqual(status, 200)
        stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
