# bluebox

`cereal killer...as in fruit loops?!?`

Local speech tools for macOS (Apple silicon), Debian-family and other Linux,
Windows 11, and the BSDs.

The blue box was a tone generator in the phreaking days: it played precise (digital) frequencies into a
handset and made the network do things it wasn't designed to do like give you free credits to use the payphone. 

This is /sorta/ the same trick pointed at your own machine. It generates speech and injects it
into the input path, and it listens on the way back in.

-=-~~~ Gr33ts to Captn'Crunch, Kevin Mitnick, and EFNet! ~~~-=-

| Piece | What it does |
|---|---|
| `voice_daemon.py` | The warm daemon on `127.0.0.1:8740`. Both directions: `/say` queues text to be spoken, `/transcribe` takes audio in. Models load once. |
| `say.py` | Stdlib-only client for the daemon. Fails soft: if the daemon is down it warns and exits 0, so `./say` never breaks the pipeline that called it. |
| `dictate.py` | Push-to-talk. Records the mic, transcribes (via the daemon), delivers text to the focused window. |
| `hotkey.py` | Tap-Ctrl dictation (needs `pynput`; X11 only on Linux): tap Ctrl to start recording, tap again to stop, transcribe and type into the focused window. `--paste`/`--clipboard` change delivery. |
| `tools/bbtail.s` | **Optional, Linux x86-64 only.** Prints the last transcripts from `~/.bluebox/transcripts.jsonl` (the daemon saves every recording to `~/.bluebox/audio/` and logs each transcript there). Written in assembly with no libc, for fun: `as --64 -o bbtail.o tools/bbtail.s && ld -o bbtail bbtail.o && ./bbtail 10`. |
| `tools/bbtoggle.s` | **Optional, Windows x64 only.** A tray switch written in assembly with no C runtime. Left-click the dot to turn bluebox on (starts `voice_daemon.py` and `hotkey.py` under `pythonw`) or off (stops both); gray = off, green = on. Middle-click copies your latest transcript to the clipboard. Right-click lists the last five transcripts (click one to copy it) and has "Open transcripts folder" (`~/.bluebox`: the log plus the audio recordings, which are never deleted). Build with mingw-w64 binutils: `x86_64-w64-mingw32-as tools/bbtoggle.s -o bbtoggle.o && x86_64-w64-mingw32-ld -e start --subsystem windows -o tools/bbtoggle.exe bbtoggle.o -lkernel32 -luser32 -lshell32 -lgdi32`. |
| `delivery.py` | The platform layer: clipboard, paste chord, keystroke. macOS, Linux, Windows, and the BSDs. |
| `platform_speech.py` | Backend selection, shows which TTS and which STT engine this box can actually run. |
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

Windows is the odd one out in a good way: it needs no external tools at all, because the clipboard and keystrokes come from `user32` via `ctypes`, which ships with Python. 

The BSDs share with Linux the `xdotool`/`xclip` path, `espeak-ng` and `piper` are in ports, and whisper.cpp builds from source.

The HID app is macOS/Linux only: `evdev` does not exist on BSD, so `device.py`
reports no devices there instead of crashing. `hidapi` is in ports if you ever
want to add a BSD backend, as the macOS backend is already the HID-shaped one.

Its dependencies are separate from the daemon's:

```sh
pip install -r app/requirements.txt
python3 -m app.main            # GUI, from the repo root
```

`hotkey.py` is the lightweight hotkey path, and the one most people want:
`pynput` on macOS and Windows, **X11 only on Linux** -- Wayland gives an app no
global key access at all, so there you bind `dictate --auto` in your desktop
settings instead.

## Backends

Chosen at startup from what's installed. Nothing is imported until needed, so a box with only speech-out still runs.

| | First choice | Falls back to |
|---|---|---|
| **TTS** | `mlx-audio` (Kokoro-82M) on Apple silicon | `kokoro-onnx` → `piper` → macOS `say` / `spd-say` / `espeak-ng` |
| **STT** | `parakeet-mlx` on Apple silicon | `faster-whisper` → `whisper.cpp` → `openai-whisper` CLI |

Run `python3 platform_speech.py` to see what this machine picked.

## Text delivery

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

## Free|Open|NetBSD setup

**FreeBSD 15.1 was run in a QEMU VM** (results in the table below).
OpenBSD and NetBSD have never been run but they share the code path, nothing
more. 

FreeBSD:

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

You know, the usual...

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

Loopback only, no auth — it's a local speech device, not a service.  Requests carrying an `Origin` header, a `Host` other than `127.0.0.1`/`localhost`, or a form/plain-text content type are refused, which closes cross-site POSTs and DNS rebinding.



MIT licensed.
