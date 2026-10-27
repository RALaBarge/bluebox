import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402

try:
    import numpy as np
except ImportError:  # the R1 tests need it
    np = None


class Config(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "config.json")
        patcher = mock.patch.object(config, "CONFIG_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def test_unknown_keys_are_ignored_not_fatal(self):
        with open(self.path, "w") as f:
            json.dump({"selected_device": "r1", "added_by_a_newer_version": 1}, f)
        self.assertEqual(config.AppConfig.load().selected_device, "r1")

    def test_unreadable_file_is_kept_not_overwritten(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(config.AppConfig.load().selected_device, "")
        self.assertTrue(os.path.exists(self.path + ".bak"))

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_log_is_private(self):
        with mock.patch.object(config, "LOG_DIR", os.path.join(self.dir.name, "logs")):
            config.append_log("x.log", "line")
            self.assertEqual(os.stat(os.path.join(config.LOG_DIR, "x.log")).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(config.LOG_DIR).st_mode & 0o777, 0o700)


class AudioWorker(unittest.TestCase):
    def test_thread_can_be_joined(self):
        # `_stop` used to be shadowed by an Event, which breaks join() on 3.12.
        try:
            from app import audio_worker
        except (ImportError, OSError):
            self.skipTest("sounddevice/PortAudio not available")
        with mock.patch.object(audio_worker.sd, "InputStream", side_effect=RuntimeError("no mic")):
            w = audio_worker.AudioRecorder(on_audio=lambda a: None)
            w.start()
            w.join(5)
        self.assertFalse(w.is_alive())


@unittest.skipIf(np is None, "numpy not installed")
class R1AutoStop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules.setdefault("hid", types.ModuleType("hid"))
        from app import r1_daemon
        cls.r1 = r1_daemon

    def make(self):
        d = self.r1.R1Daemon()
        d._rec = self.r1.TalkRecorder()
        d._rec._running = True            # no real microphone stream
        d._rec.arm()
        d._recording = True
        d._rec_since = 0.0
        d._stop_talk = mock.Mock()
        return d

    def feed(self, d, level, n=10):
        chunk = np.full((1600, 1), level, dtype="float32")
        d._rec.samples = []
        for _ in range(n):
            d._rec._cb(chunk, 1600, None, None)

    def test_speech_is_noticed_and_silence_then_stops(self):
        d = self.make()
        self.feed(d, 0.2)
        d._check_auto_stop(0.2)
        self.assertTrue(d._rec.saw_speech)
        d._stop_talk.assert_not_called()
        self.feed(d, 0.0)
        d._check_auto_stop(0.4)           # quiet begins
        d._stop_talk.assert_not_called()
        d._check_auto_stop(1.0)           # not long enough yet
        d._stop_talk.assert_not_called()
        d._check_auto_stop(1.7)
        d._stop_talk.assert_called_once()

    def test_silence_before_any_speech_does_not_stop(self):
        d = self.make()
        self.feed(d, 0.0)
        for t in (0.2, 1.0, 3.0, 10.0):
            d._check_auto_stop(t)
        d._stop_talk.assert_not_called()

    def test_time_cap(self):
        d = self.make()
        d._check_auto_stop(self.r1.MAX_RECORD_SECS + 1)
        d._stop_talk.assert_called_once()


if __name__ == "__main__":
    unittest.main()
