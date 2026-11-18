## Platform notes

**Delivery.** Paste is the default: one keystroke, and it survives newlines,
unicode and text starting with `-`. `--type` exists for apps that block paste.
The paste path saves and restores your clipboard; if the keystroke fails the
text is still on the clipboard rather than lost, and `dictate` prints the
transcript instead of exiting silently.

**Permissions.** macOS asks for Microphone + Accessibility (and Input
Monitoring for the HID app) on first run. On Linux, `ydotool` needs the
`input` group and a running `ydotoold`; `wtype`/`xdotool` must be run inside
the graphical session they're typing into — from a bare SSH shell they will
fail, which is expected and not a bug in this repo.

**Recording.** `--auto` waits for you to start talking, then stops after ~1.2s
of quiet (`SILENCE_SECS` in `dictate.py`); it gives up after 8s of nothing. The
speech threshold follows the microphone's own noise floor, so a quiet mic
works. Without `--auto`, press Enter to stop; when stdin is not a terminal it
stops on silence instead.
For a real hotkey, `hotkey.py` taps Ctrl; `app/` goes further with HID buttons
and window switching.

## Known gaps

- **`hotkey.py` is verified on Windows 11 only.** The macOS path (needs Input
  Monitoring) and the Linux X11 path are untested, and it cannot see keys at
  all under Wayland. It is a fresh implementation — no code is shared with the
  earlier macOS HID app.
- **OpenBSD and NetBSD are untested.** FreeBSD was run in a VM; the others
  share its code path, so read them as "should work", not "does". Real-hardware
  FreeBSD, a full desktop session, and sndio capture are also untested.
- **X11 focus-loss detection depends on the window manager.** Openbox keeps its
  own invisible focus window, so `XGetInputFocus` never reports "nowhere" while
  it runs and `insert()` can report success with no application focused. A bare
  X server (no window manager) is detected correctly.
- **Windows paste is unverified as a whole.** The clipboard and the
  `SendInput` keystroke are each verified independently (see below); the
  two-step paste in one motion was not. `--type` is verified end-to-end.
- `--say` playback confirmation is not wired: the old `SUPPRESS_WORDS` /
  `SILENCE_CONFIRMATIONS` constants were dead code and have been removed
  rather than reimplemented.
- The HID app's `vosk` engine is a stub that returns `None`.
- **Paste is verified on FreeBSD X11 but not on Linux.** Typing is verified
  end-to-end on real Debian (see below), but every paste chord tried there —
  Ctrl+V, Ctrl+Shift+V, Shift+Insert, middle-click — failed to land in the
  test target (WSLg's X server plus xterm). On FreeBSD, Ctrl+V landed in a Tk
  entry under Xvfb with Openbox and the old clipboard was restored, so the
  chord and the clipboard handling are sound; xterm simply does not bind
  Ctrl+V to paste. If paste does not land in your terminal, use
  `dictate --type`, which is verified.

## What has been verified

Offline checks live in `tests/`: `python3 -m unittest discover -s tests`
(numpy is needed for the R1 tests). The table below is what was run by hand.

Tested by running:

| Check | Environment | Result |
|---|---|---|
| `type` injection into a focused window | Debian 13 / WSLg X11 | **4/4 verified** by filesystem side effect |
| `type` injection into a focused window | Windows 11 | **4/4 verified** by filesystem side effect |
| Unicode typed text | Windows 11 | **verified** — `üñïçøde 中文 тест` arrived intact |
| Dash-leading text (`--help -rf /`) | Debian 13, Windows 11 | **verified** — arrives as one argument |
| Clipboard write + read round-trip | Debian 13, Windows 11 | **verified** (unicode, newlines, tabs) |
| Real speech → transcription through the daemon | Debian 13 | **verified** — espeak-ng phrase transcribed correctly |
| CUDA → CPU fallback when CUDA libs are missing | Debian 13 / WSLg | **verified** — logs the fallback, then succeeds |
| CUDA → CPU fallback when CUDA libs are missing | Windows 11 | **verified** — `cublas64_12.dll` error falls back to CPU; SAPI phrase transcribed correctly through the daemon |
| Tap-Ctrl start/stop dictation (`hotkey.py`) | Windows 11 | **verified** — real mic, daemon transcription, typed into the focused window |
| HTTP surface and error codes | Debian 13 + Windows | **verified** (200/400/404 as documented) |
| No-focus detection and honest failure | Debian 13 | **verified** — reports failure with a fix suggestion instead of false success |
| Re-focusing the last target when focus is lost | simulated `xdotool` | **logic verified with fakes** — reactivates then pastes; honest error when the window is gone or refuses focus; never activates while something already has focus |
| Paste (`Ctrl+V`) into a focused window | Debian 13 / WSLg | **not working in this environment**; typing works, so the cause is likely WSLg's X server rather than the chord |
| Paste (`Ctrl+V`) into a focused window | Windows 11 | **not directly verified**; the clipboard and `SendInput` halves are both verified independently |
| Live microphone capture | — | **not tested** — WSL exposes no usable input device |
| macOS paths | — | **not re-tested**; changed only via the shared `_run`/clipboard helpers |
| `type` injection incl. dash-leading and unicode text | FreeBSD 15.1 VM / Xvfb + Openbox | **verified** into an xterm in raw mode |
| Paste (`Ctrl+V`) + clipboard restore | FreeBSD 15.1 VM / Xvfb + Openbox | **verified** into a Tk entry; original clipboard restored |
| No-focus detection and refusal | FreeBSD 15.1 VM / bare Xvfb | **verified** — honest failure; `--clipboard` still works; stale target id refused |
| Daemon HTTP surface, request guards, `/skip` | FreeBSD 15.1 VM | **verified** — 200/400/403/415 as documented; `/skip` ends the utterance |
| Real speech → transcription (`whisper.cpp` base.en) | FreeBSD 15.1 VM | **verified** |
| Live microphone capture over OSS | FreeBSD 15.1 VM, QEMU HD Audio on the host mic (SDL backend) | **verified** — spoken sentences transcribed. The emulated mic delivers ~half real time and is quiet |
| Tap-Ctrl hotkey (`hotkey.py`) | FreeBSD 15.1 VM / Xvfb | **verified** tap detection with a stand-in recorder; Ctrl+C chord ignored |
| `--auto` end-of-speech detection | simulated levels (`tests/test_dictate.py`) | **verified in tests**; a live run on the VM was inconclusive |
| OpenBSD / NetBSD | — | **never run** |

### Linux gotchas found by testing

These would each have broken a real install, and none is visible from reading
the code:

1. **`faster-whisper` needs `av<19` on Debian 13.** `faster-whisper` 1.2.1
   calls `av.open(metadata_errors=...)`, removed in `av` 19. Every
   transcription fails with `TypeError: open() got an unexpected keyword
   argument 'metadata_errors'` until `av` is pinned. See the install section.
2. **`device="auto"` does not mean "works".** `faster-whisper` reports CUDA
   available whenever a GPU is visible, but if the CUDA libraries are absent
   the failure only appears at the first encode
   (`Library libcublas.so.12 is not found`). The backend now probes at load
   and retries on CPU at encode time, and reports the device it actually used.
3. **`wl-copy` and `xclip` hang when stdout is a pipe.** They fork a child to
   own the selection and never close the inherited pipe, so `subprocess.run`
   with `stdout=PIPE` blocks until timeout and the clipboard write looks like
   a failure. `_run()` now defaults to discarding output, and a caller that
   wants it must ask for `capture=True`. This was the hardest one to find:
   the write "succeeded" from the tool's point of view, the failure was
   swallowed by a broad `except`, and the only symptom was that *paste* did
   not work — several steps away from the cause.
4. **`WAYLAND_DISPLAY` being set does not mean Wayland.** WSLg sets
   `WAYLAND_DISPLAY=wayland-0` next to a fully working X server on `DISPLAY=:0`,
   with no wlroots compositor behind the Wayland socket — so `wtype` fails with
   "Compositor does not support the virtual keyboard protocol". The session
   check now probes whether X is actually reachable before choosing.
5. **`ydotool` exits 0 without typing.** With a mismatched seat or no
   `/dev/uinput` access, `ydotool type` returns success while nothing arrives.
   On X11 the code now tries `xdotool` first because its exit status is
   trustworthy, and only falls back to `ydotool`.
6. **Focus can vanish on a live X session.** A window manager restart (or
   WSLg losing focus) leaves an X session where every delivery tool exits 0
   and text goes nowhere. `insert()` detects it by asking `XGetInputFocus`
   (not just `_NET_ACTIVE_WINDOW`, which a non-EWMH WM never maintains and
   which therefore produced false "no focus" verdicts), then re-focuses the
   last window it delivered to. Only if that window is gone, or the WM
   refuses the activation, does it report failure — with an actionable
   message instead of claiming success. `--clipboard` still works, because
   copying needs no focus.

### Windows gotchas found by testing

7. **`ctypes.windll` truncates every handle to 32 bits.** It defaults each
   `restype` to `c_int`, so a 64-bit `HANDLE` from `GetClipboardData` comes
   back as garbage (we saw `-211105424` for a real pointer), `GlobalLock` then
   fails, and reads return empty while writes report success. `_win_api()`
   declares the signatures explicitly; that single change is the difference
   between working and not.
8. **`SetForegroundWindow` is refused from a background process.** During
   testing a script tried to focus Notepad, was silently denied, and the
   keystrokes landed in whatever window *was* in front. Nothing was harmed —
   they were Ctrl-A/Ctrl-C — but it is a real hazard: always confirm the
   foreground title before sending text, which is why `have_delivery()`
   reports it.
9. **Never trust a write-then-immediately-read clipboard test.** Reading
   straight after writing races clipboard propagation and fails
   non-deterministically — different cases failed on different runs. The
   shipped `insert()` already waits between write and keystroke; a test that
   skips the wait is testing itself, not the code.
