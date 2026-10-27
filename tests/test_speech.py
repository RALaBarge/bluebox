import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import platform_speech as ps  # noqa: E402


class PowerShellSpeech(unittest.TestCase):
    def test_text_never_enters_the_script(self):
        # U+2019 closes a PowerShell single-quoted string.
        text = "don’t'); calc #"
        cmd, env = ps._powershell_speak_cmd("powershell", text, 1.0)
        self.assertFalse(any(text in part or "calc" in part for part in cmd))
        self.assertIn("$env:BLUEBOX_TEXT", cmd[-1])
        self.assertEqual(env["BLUEBOX_TEXT"], text)


class SystemVoice(unittest.TestCase):
    def test_dash_leading_text_is_not_a_flag(self):
        tts = object.__new__(ps._SayTTS)
        tts.cmd = ["espeak-ng"]
        with mock.patch.object(ps, "IS_WINDOWS", False), \
                mock.patch.object(ps, "_run_tracked", return_value=0) as run:
            tts.speak("--help", None, 1.0, lambda: False)
        self.assertEqual(run.call_args.args[0][-2:], ["--", "--help"])


class StopPlayback(unittest.TestCase):
    def test_terminates_a_running_utterance(self):
        result = {}

        def speak():
            result["rc"] = ps._run_tracked(
                [sys.executable, "-c", "import time; time.sleep(60)"])

        t = threading.Thread(target=speak)
        t.start()
        time.sleep(1.0)
        self.assertTrue(t.is_alive())
        ps.stop_playback()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertNotEqual(result["rc"], 0)


if __name__ == "__main__":
    unittest.main()
