# bluebox

`cereal killer...as in fruit loops?!?`

Local speech tools for macOS (Apple silicon), Debian-family and other Linux,
Windows 11, and — wired up but untested — the BSDs.

**Generate the tones. Seize the trunk.**

The blue box was a tone generator: it played precise frequencies into a
handset and made the network do things it wasn't designed to do. This is the
same trick pointed at your own machine -- it generates speech and injects it
into the input path, and it listens on the way back in.

-=-~~~ Gr33ts to Captn'Crunch, Kevin Mitnick, and EFNet! ~~~-=-

| Piece | What it does |
|---|---|
| `voice_daemon.py` | The warm daemon on `127.0.0.1:8740`. Both directions: `/say` queues text to be spoken, `/transcribe` takes audio in. Models load once. |
| `say.py` | Stdlib-only client for the daemon. Fails soft: if the daemon is down it warns and exits 0, so `./say` never breaks the pipeline that called it. |
| `dictate.py` | Push-to-talk. Records the mic, transcribes (via the daemon), delivers text to the focused window. |
| `hotkey.py` | Tap-Ctrl dictation (needs `pynput`; X11 only on Linux): tap Ctrl to start recording, tap again to stop, transcribe and type into the focused window. `--paste`/`--clipboard` change delivery. |
| `delivery.py` | The platform layer: clipboard, paste chord, keystroke. macOS, Linux, Windows, and the BSDs. |
| `platform_speech.py` | Backend selection — which TTS and which STT engine this box can actually run. |
| `app/` | **Optional, and nothing at the root imports it.** One flat package (PyQt6; evdev on Linux, hidapi on macOS) mapping HID device buttons to speech-to-text and window actions. It consumes the daemon's `/transcribe` and delegates text insertion to `delivery.py`, keeping only window activation. Entry points: `python3 -m app.main` (GUI), `-m app.ctrl_dictate` (macOS Ctrl-tap), `-m app.r1_daemon` (macOS only: headless R1 buttons), `-m app.sniff_r1` (HID diagnostics). |

```sh
python3 voice_daemon.py &        # one process, both directions
./say "build finished"           # queue some speech
./say --status                   # queue depth, backends, uptime
./dictate --auto                 # record until you stop talking, paste into the focused window
./dictate --check                # what works on this machine
python3 hotkey.py                # tap Ctrl to start recording, tap Ctrl again to finish
```

## Platforms

The same Python runs everywhere; nothing is compiled, and every backend is
probed at startup.

| Platform | Speech out | Speech in | Text delivery | Status |
|---|---|---|---|---|
| macOS (Apple silicon) | `mlx-audio` (Kokoro-82M) → `say` | `parakeet-mlx` → faster-whisper → whisper.cpp | `pbcopy` + `osascript` | supported |
| Linux (X11) | `kokoro-onnx` / `piper` / `espeak-ng` | faster-whisper / whisper.cpp | `xdotool` / `xclip` | supported, verified |
| Linux (Wayland) | as X11 | as X11 | `ydotool` / `wtype` / `wl-copy` | supported — typing verified, paste unverified |
| Windows 11 | SAPI via PowerShell | faster-whisper / whisper.cpp | `SendInput` + Win32 clipboard | supported, verified |
| FreeBSD (15.1) | `espeak-ng` / `piper` | whisper.cpp | `xdotool` / `xclip` | supported, verified in a VM (type, paste, daemon, hotkey, live mic) |
| OpenBSD / NetBSD | `espeak-ng` / `piper` | whisper.cpp | `xdotool` / `xclip` | **wired up, never run** |

Windows is the odd one out in a good way: it needs no external tools at all,
because the clipboard and keystrokes come from `user32` via `ctypes`, which
ships with Python. The BSDs are the closest fit to Linux — they share the
`xdotool`/`xclip` path, `espeak-ng` and `piper` are in ports, and whisper.cpp
builds from source.

The HID app is macOS/Linux only: `evdev` does not exist on BSD, so `device.py`
reports no devices there instead of crashing. `hidapi` is in ports if you ever
want to add a BSD backend — the macOS backend is already the HID-shaped one.
Its dependencies are separate from the daemon's:

```sh
pip install -r app/requirements.txt
python3 -m app.main            # GUI, from the repo root
```

`hotkey.py` is the lightweight hotkey path, and the one most people want:
`pynput` on macOS and Windows, **X11 only on Linux** — Wayland gives an app no
global key access at all, so there you bind `dictate --auto` in your desktop
settings instead.

## Backends

Chosen at startup from what's installed. Nothing is imported until needed, so a
box with only speech-out still runs.

| | First choice | Falls back to |
|---|---|---|
| **TTS** | `mlx-audio` (Kokoro-82M) on Apple silicon | `kokoro-onnx` → `piper` → macOS `say` / `spd-say` / `espeak-ng` |
| **STT** | `parakeet-mlx` on Apple silicon | `faster-whisper` → `whisper.cpp` → `openai-whisper` CLI |

Run `python3 platform_speech.py` to see what this machine picked.

## Text delivery, by platform

`delivery.py` puts the transcript into whatever window has focus. Each
platform needs a different mechanism:

| Platform | Clipboard | Paste chord | Typing |
|---|---|---|---|
| macOS | `pbcopy`/`pbpaste` | `osascript` Cmd-V | `osascript keystroke` |
| Linux (X11) | `xclip` | `xdotool key ctrl+v` | `xdotool type` |
| Linux (Wayland) | `wl-copy`/`wl-paste` | `ydotool` / `wtype` | `ydotool` / `wtype` |
| BSD (X11) | `xclip` | `xdotool key ctrl+v` | `xdotool type` |
| Windows | `ctypes` (CF_UNICODETEXT), `clip.exe` fallback | `SendInput` Ctrl-V | `SendInput` Unicode scancodes |

Windows needs **no external tools** — `SendInput` and the clipboard come from
`user32` via `ctypes`, which ships with Python. Typing uses
`KEYEVENTF_UNICODE` scancodes rather than virtual keys, so text is not
affected by the active keyboard layout and any character the target can
display can be sent.

One asymmetry worth knowing: on X11 `insert()` can tell that no window has
focus, because `XGetInputFocus` reports `None` or `PointerRoot`. It does not
simply give up: it re-focuses the last window it delivered to and only reports
failure if that window is gone or refuses the activation. The id is kept in
`$XDG_RUNTIME_DIR/bluebox-last-window` precisely because `dictate.py` is a new
process every run, so in-memory state would never survive to be useful.
Windows has no such signal — there is always *a* foreground window — so
`have_delivery()` reports the foreground window's title instead, and it is
worth checking that the app you expect is actually in front.
`SetForegroundWindow` is refused unless the calling process already owns the
foreground, so a script cannot reliably bring a window forward before
dictating.

## Debian / Ubuntu setup

```sh
# STT — the one thing you actually need
sudo apt install python3-pip ffmpeg
pip install faster-whisper "av<19" sounddevice soundfile numpy
#   IMPORTANT: the "av<19" pin is not optional. faster-whisper 1.2.1 calls
#   av.open(metadata_errors=...), which av 19.x removed — without the pin
#   every transcription dies with "TypeError: open() got an unexpected
#   keyword argument 'metadata_errors'". Verified on Debian 13 (trixie).
#   first run downloads the model; VOICE_WHISPER_SIZE=base.en is the default
#   set VOICE_WHISPER_SIZE=small.en for better accuracy

# Tap-Ctrl hotkey (optional, hotkey.py)
pip install pynput

# TTS — pick one
pip install kokoro-onnx          # closest to the macOS Kokoro voices
# or: sudo apt install espeak-ng  # no model download, robotic but instant

# Text delivery — pick one, matching your session type
sudo apt install ydotool         # Wayland or X11; needs ydotoold running
sudo apt install wtype           # wlroots Wayland (Sway, Hyprland, sway-based)
sudo apt install xdotool xclip   # X11

# Wayland clipboard (optional, improves paste fidelity)
sudo apt install wl-clipboard
```

`ydotool` needs its daemon and access to `/dev/uinput`:

```sh
sudo systemctl enable --now ydotoold
sudo usermod -aG input "$USER"     # log out and back in
```

Then check what you got:

```sh
python3 dictate.py --check
```

Expected output on a working Debian box (this is real output from Debian 13
under WSL, with `espeak-ng` for TTS):

```
platform : Linux
session  : wayland
delivery : OK — Linux via ydotool, wtype, xdotool
tts      : system (espeak-ng)
stt      : faster-whisper (base.en, cpu)
daemon   : up — tts=system (espeak-ng) stt=faster-whisper
```

`stt` may report `cuda` if a GPU is visible. If the CUDA libraries aren't
actually installed, the first transcription logs a fallback and switches to
CPU automatically rather than crashing — WSL triggers this, since it exposes
the GPU without shipping the CUDA runtime.

## FreeBSD / OpenBSD / NetBSD setup

**FreeBSD 15.1 was run in a QEMU VM** (results in the table below).
OpenBSD and NetBSD have never been run; they share the code path, nothing
more. FreeBSD:

```sh
pkg install python312 py312-numpy py312-soundfile py312-sounddevice \
    espeak-ng xdotool xclip whisper.cpp ffmpeg
pkg install py312-pynput py312-python-xlib     # only for hotkey.py
```

`pip install pynput` is not enough on a BSD: pynput's dependency on
`python-xlib` is marked Linux-only, so pip skips it and `hotkey.py` cannot
import. Use the packages above.

```sh
# STT: whisper.cpp is the reliable path — there is no ctranslate2 wheel for
# BSD, so faster-whisper usually loses. whisper.cpp is in packages
# (`whisper-cli`); download a ggml model and point the backend at it:
export WHISPER_CPP_MODEL=/path/to/ggml-base.en.bin
python3.12 dictate.py --check   # expect: delivery : OK — FreeBSD via xdotool, xclip
```

- `delivery.py` treats the BSDs as one family with Linux — same X11 tools, no
  `pbcopy`/`osascript` — so `have_delivery()` reports
  `FreeBSD via xdotool, xclip` instead of `unsupported platform`.
- `platform_speech.py` needed no changes at all: it already falls through to
  `espeak-ng`/`spd-say` for TTS and to whisper.cpp/openai-whisper for STT.
- **Audio capture** works on FreeBSD through OSS (`/dev/dsp`): a live
  recording through the daemon was transcribed correctly. OpenBSD uses sndio,
  which has not been tried. `sounddevice` needs a working backend (`sndio`/
  `OSS`/`pulse` varies by BSD and by build), so check that before blaming the
  rest:

  ```sh
  python3 -c "import sounddevice; print(sounddevice.query_devices())"
  ```

## Running it as a service

**Linux (systemd, user unit):**

```sh
mkdir -p ~/.config/systemd/user
cp deploy/bluebox-daemon.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now bluebox-daemon
journalctl --user -u bluebox-daemon -f
```

**macOS (launchd):** edit `REPLACE_ME` in the plist to your home directory, then:

```sh
cp deploy/com.bluebox.daemon.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.bluebox.daemon.plist
tail -f /tmp/bluebox-daemon.log
```

## API

```
POST /say         {"text": "...", "voice": "af_heart", "speed": 1.0, "source": "..."}
                  -> {"ok": true, "queued": N}
POST /transcribe  body = raw WAV (Content-Type: audio/wav)
                  or {"audio_b64": "...", "format": "wav"}
                  -> {"text": "..."}
POST /skip        stop the current utterance
POST /clear       drop the queue (keeps the current utterance)
GET  /status      queue depth, now playing, backends, counters
GET  /health      {"ok": true, ...}
```

Loopback only, no auth — it's a local speech device, not a service. Anything
that can already run code as you can also just play audio. A web page you
visit cannot: requests carrying an `Origin` header, a `Host` other than
`127.0.0.1`/`localhost`, or a form/plain-text content type are refused, which
closes cross-site POSTs and DNS rebinding.

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

MIT licensed.
