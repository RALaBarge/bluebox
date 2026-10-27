import http.server
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dictate  # noqa: E402

STEP = 0.05
QUIET, SPEECH_QUIET_MIC, SPEECH_LOUD = 0.001, 0.006, 0.05


def stop_time(levels):
    """Feed one level per STEP seconds; return when the detector says stop."""
    det = dictate.EndpointDetector()
    for i, level in enumerate(levels):
        if det.feed(i * STEP, level):
            return i * STEP
    return None


def secs(level, n):
    return [level] * int(n / STEP)


class EndpointDetection(unittest.TestCase):
    def test_slow_start_is_not_cut_off(self):
        t = stop_time(secs(QUIET, 3) + secs(SPEECH_LOUD, 2) + secs(QUIET, 3))
        self.assertGreater(t, 5.0)           # not before the speech ended
        self.assertLess(t, 5.0 + 1.2 + 0.2)  # and soon after it

    def test_quiet_microphone_still_counts_as_speech(self):
        t = stop_time(secs(QUIET, 1) + secs(SPEECH_QUIET_MIC, 2) + secs(QUIET, 3))
        self.assertIsNotNone(t)
        self.assertGreater(t, 3.0)

    def test_speech_from_the_first_instant(self):
        t = stop_time(secs(SPEECH_LOUD, 2) + secs(QUIET, 3))
        self.assertGreater(t, 2.0)
        self.assertLess(t, 2.0 + 1.2 + 0.2)

    def test_short_pause_inside_speech_does_not_stop(self):
        levels = (secs(QUIET, 1) + secs(SPEECH_LOUD, 1) + secs(QUIET, 0.6)
                  + secs(SPEECH_LOUD, 1) + secs(QUIET, 3))
        self.assertGreater(stop_time(levels), 3.6)

    def test_gives_up_when_nothing_is_said(self):
        t = stop_time(secs(QUIET, 12))
        self.assertAlmostEqual(t, dictate.NO_SPEECH_SECS, delta=0.2)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = b'{"ok": false, "error": "no STT backend available"}'
        self.send_response(500)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class DaemonReplies(unittest.TestCase):
    def test_error_from_a_running_daemon_is_not_treated_as_down(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}"
            with mock.patch.object(dictate, "DAEMON", url):
                with self.assertRaisesRegex(RuntimeError, "no STT backend"):
                    dictate.transcribe_via_daemon(b"RIFF")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_no_daemon_returns_none(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with mock.patch.object(dictate, "DAEMON", f"http://127.0.0.1:{port}"):
            self.assertIsNone(dictate.transcribe_via_daemon(b"RIFF"))


if __name__ == "__main__":
    unittest.main()
