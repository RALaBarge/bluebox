import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delivery  # noqa: E402


class WindowsTyping(unittest.TestCase):
    def test_astral_characters_become_surrogate_pairs(self):
        self.assertEqual(delivery._utf16_units("a\U0001F600"),
                         [0x61, 0xD83D, 0xDE00])

    def test_scancode_keeps_all_sixteen_bits(self):
        down = delivery._win_unicode(0xD83D)
        up = delivery._win_unicode(0xD83D, up=True)
        self.assertEqual(down.ki.wScan, 0xD83D)
        self.assertEqual(up.ki.wScan, 0xD83D)
        self.assertEqual(down.ki.dwFlags, delivery._KEYEVENTF_UNICODE)
        self.assertEqual(up.ki.dwFlags,
                         delivery._KEYEVENTF_UNICODE | delivery._KEYEVENTF_KEYUP)


class X11Probe(unittest.TestCase):
    def test_probe_result_is_reused(self):
        delivery._x11_cache = None
        with mock.patch.dict("os.environ", {"DISPLAY": ":99"}), \
                mock.patch.object(delivery, "_x11_probe", return_value=True) as p:
            self.assertTrue(delivery._x11_usable())
            self.assertTrue(delivery._x11_usable())
        self.assertEqual(p.call_count, 1)
        delivery._x11_cache = None

    def test_a_different_display_is_probed_again(self):
        delivery._x11_cache = None
        with mock.patch.object(delivery, "_x11_probe", return_value=True) as p:
            with mock.patch.dict("os.environ", {"DISPLAY": ":1"}):
                delivery._x11_usable()
            with mock.patch.dict("os.environ", {"DISPLAY": ":2"}):
                delivery._x11_usable()
        self.assertEqual(p.call_count, 2)
        delivery._x11_cache = None


@unittest.skipUnless(sys.platform == "win32", "Windows clipboard")
class WindowsClipboard(unittest.TestCase):
    def test_text_on_the_clipboard_is_not_flagged_as_non_text(self):
        saved = delivery._clipboard_read()
        try:
            self.assertTrue(delivery._clipboard_write("tést".encode()))
            self.assertFalse(delivery._win_clipboard_has_non_text())
        finally:
            if saved:
                delivery._clipboard_write(saved)


if __name__ == "__main__":
    unittest.main()
