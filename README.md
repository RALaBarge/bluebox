# voice

Local speech tools for macOS (Apple silicon), with a Linux path for the input device app.

| Piece | What it does |
|---|---|
| `dictate.py` | Push-to-talk dictation. Records the mic, transcribes locally with `parakeet-mlx`, pastes or types the text into the focused window (or prints it). |
| `tts_daemon.py` + `say.py` | A text-to-speech daemon on `127.0.0.1:8740` (Kokoro via `mlx-audio`). Anything can POST text; utterances queue and play one at a time. `say.py` is a stdlib-only client that fails soft if the daemon is down. |
| `finger_mouse_stt_app/` | A PyQt6 app that maps buttons on a HID pointing device to speech-to-text and window actions (evdev on Linux, hidapi on macOS). See its `AGENTS.md` for the cross-platform notes. |

```sh
python3 tts_daemon.py &          # speaks anything sent to it
./say "build finished"           # queue some speech
./dictate --auto                 # record until you stop talking, paste into the focused window
```

macOS asks for Microphone, Accessibility and (for the HID app) Input Monitoring permission the first time.

MIT licensed.
