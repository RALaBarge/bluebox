#!/usr/bin/env python3.12
"""Cross-platform text delivery: get the transcript into the focused window.

One API for both platforms, stdlib-only, no imports that don't exist on the
other OS:

    insert(text, mode="paste")   -> paste (clipboard + synthetic Cmd/Ctrl-V)
    insert(text, mode="type")    -> keystroke the text
    insert(text, mode="clipboard")-> clipboard only, no keystroke
    have_delivery()              -> (bool, reason) can we deliver at all?

macOS uses pbcopy/pbpaste + osascript. Linux uses whatever's installed, in
preference order, because no single tool covers both Wayland and X11:

    ydotool    wayland (and X11), needs ydotoold running
    wtype      wlroots Wayland, native typing
    xdotool    X11
    wl-copy    Wayland clipboard, paired with ydotool for the Ctrl-V
    xclip      X11 clipboard, paired with xdotool for the Ctrl-V

Why paste-by-default rather than type: pasting is one keystroke and handles
newlines, unicode and CLI flags in the text; typing a 400-char transcript
character-by-character is slow and drops characters under load. The `type`
path exists only for apps that block paste, same as the macOS original.

Clipboard save/restore is deliberately best-effort: we restore what was there
before, and we never fail the utterance over it. Note that on Linux the
clipboard can die with the process that owns it (X11 semantics), which is why
the paste path re-uses one helper process rather than spawning a helper per
call.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time

# ctypes exists on every platform, but the Windows section below only runs on
# Windows; importing it at module level keeps the structure flat and costs
# nothing measurable.
import ctypes

IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"
IS_WINDOWS = platform.system() == "Windows"

# How long to wait after placing the clipboard before sending the paste key.
# Too short and the key arrives before the clipboard is populated, which is the
# classic "pasted the previous clipboard" bug.
_PASTE_SETTLE = 0.15
# How long to wait for the paste to land before restoring the old clipboard.
_RESTORE_SETTLE = 0.4


def _which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _run(cmd: list[str], data: bytes | None = None, timeout: float = 5.0,
         check: bool = False,
         capture: bool = False) -> subprocess.CompletedProcess:
    """Run a helper command. Output is discarded unless you ask for it.

    `capture` defaults to **False** on purpose. A tool that forks a child
    owning some resource — a clipboard selection, a daemon connection — keeps
    the inherited stdout open, so `stdout=PIPE` never sees EOF and
    subprocess.run blocks until the timeout. The command looked like a
    failure when it had actually worked.

    That is not hypothetical: it silently broke every clipboard write on
    Linux. `wl-copy` and `xclip` both hang for the full timeout with a pipe
    and return in milliseconds with DEVNULL, verified on Debian 13. When the
    failure was swallowed by a broad `except`, the only symptom was that
    paste did not work, several steps away from the cause.

    So the default is the safe one, and a caller that genuinely wants the
    output types `capture=True` and owns the consequences. Only the clipboard
    *readers* need it, and they read from tools that do not fork-and-linger.
    """
    return subprocess.run(
        cmd,
        input=data,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=check,
    )


# --------------------------------------------------------------------------
# windows (ctypes; no third-party dependency)
# --------------------------------------------------------------------------

# Windows has one input API that matters — SendInput — and one clipboard. Both
# are reachable through ctypes, so this platform needs no pip install, which
# is the main reason it is implemented here rather than shelled out to
# PowerShell (spawning powershell.exe per utterance costs ~300ms and breaks
# if execution policy is locked down).

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_VK_CONTROL, _VK_V = 0x11, 0x56


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def _win_key(vk: int, up: bool = False) -> _INPUT:
    return _INPUT(type=_INPUT_KEYBOARD,
                  ki=_KEYBDINPUT(wVk=vk, wScan=0,
                                 dwFlags=_KEYEVENTF_KEYUP if up else 0,
                                 time=0, dwExtraInfo=None))


def _win_unicode(ch: str, up: bool = False) -> _INPUT:
    """A single UTF-16 code unit typed as a Unicode scancode.

    Using KEYEVENTF_UNICODE rather than virtual keys means we never touch the
    keyboard layout: any character the target can display can be sent,
    including ones with no VK code at all.
    """
    return _INPUT(type=_INPUT_KEYBOARD,
                  ki=_KEYBDINPUT(wVk=0, wScan=ord(ch),
                                 dwFlags=_KEYEVENTF_UNICODE |
                                 (_KEYEVENTF_KEYUP if up else 0),
                                 time=0, dwExtraInfo=None))


def _win_send(inputs: list) -> bool:
    if not inputs:
        return True
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    sent = ctypes.windll.user32.SendInput(n, arr, ctypes.sizeof(_INPUT))
    return sent == n


def _win_type_text(text: str) -> bool:
    """Type via Unicode scancodes, in batches to bound the call size."""
    batch: list = []
    for ch in text:
        # Newline: use VK_RETURN, since a literal \n is not a keystroke.
        if ch == "\n":
            batch.append(_win_key(0x0D))
            batch.append(_win_key(0x0D, up=True))
        elif ch == "\t":
            batch.append(_win_key(0x09))
            batch.append(_win_key(0x09, up=True))
        else:
            batch.append(_win_unicode(ch))
            batch.append(_win_unicode(ch, up=True))
        if len(batch) >= 128:
            if not _win_send(batch):
                return False
            batch = []
    return _win_send(batch)


def _win_send_paste() -> bool:
    return _win_send([
        _win_key(_VK_CONTROL),
        _win_key(_VK_V),
        _win_key(_VK_V, up=True),
        _win_key(_VK_CONTROL, up=True),
    ])


def _win_api():
    """Return (user32, kernel32) with correct 64-bit signatures.

    ctypes.windll defaults every restype to C int (32-bit). HANDLE and HGLOBAL
    are pointer-sized, so on 64-bit Windows they get silently truncated and
    every clipboard call misbehaves — GetClipboardData returns a garbage
    value, GlobalLock then fails, and the read comes back empty even though
    the write succeeded. Declaring the signatures once here is what makes the
    difference between working and not.
    """
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32

    u32.OpenClipboard.argtypes = [ctypes.c_void_p]
    u32.OpenClipboard.restype = ctypes.c_bool
    u32.CloseClipboard.restype = ctypes.c_bool
    u32.EmptyClipboard.restype = ctypes.c_bool
    u32.GetClipboardData.argtypes = [ctypes.c_uint]
    u32.GetClipboardData.restype = ctypes.c_void_p
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    u32.IsClipboardFormatAvailable.restype = ctypes.c_bool

    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.restype = ctypes.c_bool
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.restype = ctypes.c_void_p

    return u32, k32


def _win_clipboard_read() -> bytes:
    """Read CF_UNICODETEXT from the Windows clipboard."""
    u32, k32 = _win_api()
    if not u32.OpenClipboard(None):
        return b""
    try:
        CF_UNICODETEXT = 13
        handle = u32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return b""
        ptr = k32.GlobalLock(handle)
        if not ptr:
            return b""
        try:
            return ctypes.wstring_at(ptr).encode("utf-8", errors="replace")
        finally:
            k32.GlobalUnlock(handle)
    finally:
        u32.CloseClipboard()


def _win_clipboard_write(data: bytes) -> bool:
    """Set CF_UNICODETEXT, taking ownership of a global memory block.

    GMEM_MOVEABLE is required for clipboard data; the system takes ownership
    on a successful SetClipboardData, so we must not free it ourselves.
    """
    u32, k32 = _win_api()
    text = data.decode("utf-8", errors="replace")
    buf = ctypes.create_unicode_buffer(text)

    if not u32.OpenClipboard(None):
        return False
    try:
        u32.EmptyClipboard()
        GMEM_MOVEABLE = 0x0002
        size = ctypes.sizeof(buf)
        handle = k32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        ptr = k32.GlobalLock(handle)
        if not ptr:
            k32.GlobalFree(handle)
            return False
        ctypes.memmove(ptr, buf, size)
        k32.GlobalUnlock(handle)
        CF_UNICODETEXT = 13
        if not u32.SetClipboardData(CF_UNICODETEXT, handle):
            k32.GlobalFree(handle)
            return False
        return True  # ownership transferred to the clipboard
    finally:
        u32.CloseClipboard()


def _win_foreground_title() -> str:
    u32 = ctypes.windll.user32
    hwnd = u32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = u32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    u32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


# --------------------------------------------------------------------------
# session detection
# --------------------------------------------------------------------------

def is_wayland() -> bool:
    """Are we on a Wayland session *that can actually be typed into*?

    WAYLAND_DISPLAY alone is not enough. WSLg sets WAYLAND_DISPLAY=wayland-0
    alongside a fully working X server on DISPLAY=:0, but there is no wlroots
    compositor behind that socket, so wtype fails with "Compositor does not
    support the virtual keyboard protocol". Trusting the env var alone sent
    text to a tool that could not deliver it and never tried xdotool.

    So: prefer X11 when DISPLAY is set and reachable, and only call it Wayland
    when there is no usable X server.
    """
    import os
    if not (os.environ.get("WAYLAND_DISPLAY")
            or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"):
        return False
    return not _x11_usable()


def _x11_usable() -> bool:
    """True if DISPLAY points at an X server we can actually talk to."""
    import os
    if not os.environ.get("DISPLAY"):
        return False
    # xdpyinfo is the cheap, definitive probe; xset is a common fallback.
    for cmd in (["xdpyinfo"], ["xset", "q"]):
        exe = _which(cmd[0])
        if not exe:
            continue
        try:
            r = subprocess.run([exe] + cmd[1:], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=3)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    # No probe binary: assume X works if DISPLAY is set, since xdotool will
    # report failure honestly on its own.
    return not _which("xdpyinfo", "xset") and bool(_which("xdotool"))


# --------------------------------------------------------------------------
# clipboard
# --------------------------------------------------------------------------

def _clipboard_read() -> bytes:
    if IS_WINDOWS:
        # ctypes first: it is instant and needs no PATH lookup. clip.exe
        # cannot read, only write, so it is no use here.
        try:
            return _win_clipboard_read()
        except (OSError, ctypes.ArgumentError):
            return b""
    if IS_MACOS:
        try:
            return _run(["pbpaste"], capture=True).stdout
        except (OSError, subprocess.SubprocessError):
            return b""
    # Linux: prefer wl-clipboard on Wayland, xclip on X11.
    if is_wayland() and _which("wl-paste"):
        try:
            return _run(["wl-paste", "--no-newline"], capture=True).stdout
        except (OSError, subprocess.SubprocessError):
            return b""
    p = _which("xclip")
    if p:
        try:
            return _run([p, "-selection", "clipboard", "-o"],
                        capture=True).stdout
        except (OSError, subprocess.SubprocessError):
            return b""
    return b""


def _clipboard_write(data: bytes) -> bool:
    if IS_WINDOWS:
        try:
            if _win_clipboard_write(data):
                return True
        except (OSError, ctypes.ArgumentError):
            pass
        # Fallback: clip.exe ships with Windows and needs no ctypes wrangling.
        # It only handles text and always writes CF_UNICODETEXT, which is all
        # we ever put on the clipboard.
        if _which("clip", "clip.exe"):
            try:
                _run([_which("clip", "clip.exe")], data=data, check=True)
                return True
            except (OSError, subprocess.SubprocessError):
                return False
        return False

    if IS_MACOS:
        try:
            _run(["pbcopy"], data=data, check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    # Output is discarded (the default): these tools fork a child that owns
    # the selection and leaves the inherited stdout open, so a pipe would
    # block until the timeout. See _run's docstring.
    if is_wayland() and _which("wl-copy"):
        try:
            # wl-copy forks to own the selection; --paste-once would drop it
            # after one paste, which breaks the restore dance.
            _run([_which("wl-copy")], data=data, check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            pass

    p = _which("xclip")
    if p:
        try:
            _run([p, "-selection", "clipboard"], data=data, check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            return False
    return False


# --------------------------------------------------------------------------
# keystroke / paste
# --------------------------------------------------------------------------

def _send_paste_key() -> bool:
    """Synthesise the platform paste chord."""
    if IS_WINDOWS:
        # Ctrl+V via SendInput. No window-focus precheck is possible the way
        # X11 allows, because Windows always has *a* foreground window; if it
        # is not the one you meant, the text still goes somewhere.
        try:
            return _win_send_paste()
        except (OSError, ctypes.ArgumentError):
            return False

    if IS_MACOS:
        try:
            _run([
                "osascript", "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ], check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    # Linux. On X11 use xdotool first for the same reason as _type_text:
    # ydotool's exit status does not prove the chord landed.
    on_x11 = _x11_usable()
    if on_x11 and _which("xdotool"):
        try:
            _run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    if _which("ydotool"):
        try:
            _run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    if _which("wtype"):
        try:
            _run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"], check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    if not on_x11 and _which("xdotool"):
        try:
            _run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    return False


def _type_text(text: str) -> bool:
    """Keystroke the text. Returns True only if a tool reported success.

    Ordering note: xdotool is tried on X11 sessions *before* ydotool, because
    ydotool exits 0 even when ydotoold silently discards the event (mismatched
    seat, no /dev/uinput access, wrong DISPLAY). That makes it a false
    positive, and it would otherwise mask a perfectly good X11 path.
    """
    if IS_WINDOWS:
        try:
            return _win_type_text(text)
        except (OSError, ctypes.ArgumentError):
            return False

    if IS_MACOS:
        esc = text.replace("\\", "\\\\").replace('"', '\\"')
        try:
            _run([
                "osascript", "-e",
                f'tell application "System Events" to keystroke "{esc}"',
            ], check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    on_x11 = _x11_usable()

    # On X11, xdotool first: its exit status reflects whether the event landed.
    if on_x11 and _which("xdotool"):
        try:
            _run(["xdotool", "type", "--clearmodifiers", "--", text],
                 check=True, timeout=30)
            return True
        except (OSError, subprocess.SubprocessError):
            pass

    if _which("ydotool"):
        try:
            _run(["ydotool", "type", "--", text], check=True, timeout=30)
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    if _which("wtype"):
        try:
            _run(["wtype", "--", text], check=True, timeout=30)
            return True
        except (OSError, subprocess.SubprocessError):
            pass

    # Last resort: if we skipped xdotool because we thought this was Wayland,
    # try it anyway rather than give up.
    if not on_x11 and _which("xdotool"):
        try:
            _run(["xdotool", "type", "--clearmodifiers", "--", text],
                 check=True, timeout=30)
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    return False


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def have_delivery() -> tuple[bool, str]:
    """Can we deliver text at all? Returns (ok, human-readable reason).

    This checks that the *tools* exist and that there is somewhere to deliver
    to. It cannot promise a keystroke will land: on X11 nothing is delivered
    without a focused window, and a session can be alive with no window
    focused at all. That happens in practice (a WM restarting, or WSLg losing
    focus), and the symptom is a transcript that types into nothing while
    every tool exits 0.
    """
    if IS_MACOS:
        if not (shutil.which("osascript") and shutil.which("pbcopy")):
            return False, "macOS: pbcopy/osascript missing"
        return True, "macOS pbcopy + osascript"

    if IS_WINDOWS:
        # No external tool is required: SendInput and the clipboard come from
        # user32 via ctypes, which is present wherever Python is.
        #
        # Note the asymmetry with X11: Windows always has *a* foreground
        # window, so we cannot detect "nowhere to deliver". The title is
        # reported so the caller can see where text will actually land —
        # SetForegroundWindow is refused if the calling process does not own
        # the foreground, which is how a dictation can end up in the wrong app.
        title = ""
        try:
            title = _win_foreground_title()
        except (OSError, ctypes.ArgumentError):
            pass
        note = "Windows SendInput"
        if title:
            note += f" (foreground: {title[:40]!r})"
        return True, note

    if IS_LINUX:
        found = [t for t in ("ydotool", "wtype", "xdotool") if _which(t)]
        if not found:
            return False, ("Linux: no input tool found — install one of "
                           "ydotool (Wayland/X11), wtype (wlroots), "
                           "or xdotool (X11)")
        note = "Linux via " + ", ".join(found)
        if _x11_usable():
            # Tell the caller whether a target exists, since that determines
            # whether insert() can do anything.
            r = subprocess.run(["xdotool", "getactivewindow"],
                               capture_output=True, text=True, timeout=3) \
                if _which("xdotool") else None
            if r is not None and not r.stdout.strip():
                note += " — WARNING: no focused window on X11, delivery will " \
                        "go nowhere (click a window first)"
        return True, note

    return False, f"unsupported platform: {platform.system()}"


def _no_focus_expected() -> bool:
    """True when we're on X11 with no focused window, so keys go nowhere.

    Observed in WSLg: the session is alive, every tool exits 0, and text is
    delivered into the void because no window holds focus. Reporting success
    there is worse than reporting failure, since the caller then believes the
    transcript landed. We can detect the condition but not fix it — granting
    focus is the window manager's job.
    """
    if not _x11_usable():
        return False
    if not _which("xdotool"):
        return False
    try:
        r = subprocess.run(["xdotool", "getactivewindow"],
                           capture_output=True, text=True, timeout=3)
        return not r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False


def insert(text: str, mode: str = "paste") -> tuple[bool, str]:
    """Deliver text to the focused window.

    mode: "paste" (default), "type", or "clipboard".
    Returns (ok, note). Never raises — callers report, they don't catch.

    Note that ok=True means "the tool reported success", which is the most we
    can know without reading the target's buffer. On X11 that is only
    meaningful if something was focused; the no-focus case is detected and
    reported as failure rather than silently claimed.
    """
    if not text:
        return False, "empty text"

    if mode == "clipboard":
        # Clipboard does not need focus, so it works even with no window.
        ok = _clipboard_write(text.encode())
        return ok, ("copied to clipboard" if ok
                    else "clipboard tool unavailable")

    # Both typing and pasting need a focused window on X11; only the
    # clipboard does not. Checking here rather than inside _type_text keeps
    # the "did it land" question in one place.
    if _no_focus_expected():
        return False, ("no focused window — text would go nowhere "
                       "(click a window, or use --clipboard to copy instead)")

    if mode == "type":
        ok = _type_text(text)
        return ok, ("typed" if ok else "no typing tool available")

    # paste: save clipboard, put text there, send the chord, restore.
    old = _clipboard_read()
    if not _clipboard_write(text.encode()):
        return False, "clipboard tool unavailable"
    time.sleep(_PASTE_SETTLE)
    if not _send_paste_key():
        # Text is on the clipboard even though the keystroke failed; that's a
        # useful degraded outcome rather than a total loss.
        return False, "paste key failed (text is on the clipboard)"
    time.sleep(_RESTORE_SETTLE)
    if old:
        _clipboard_write(old)
    return True, "pasted"


def main() -> None:
    """Tiny self-test: `python3 delivery.py --check`."""
    import argparse
    ap = argparse.ArgumentParser(description="delivery backend probe")
    ap.add_argument("--check", action="store_true", help="report capability")
    ap.add_argument("--text", default=None, help="deliver this text")
    ap.add_argument("--mode", default="paste",
                    choices=["paste", "type", "clipboard"])
    args = ap.parse_args()

    ok, why = have_delivery()
    if args.check or args.text is None:
        print(f"platform : {platform.system()}")
        print(f"session  : {'wayland' if IS_LINUX and is_wayland() else 'x11/native'}")
        print(f"delivery : {'OK' if ok else 'NO'} — {why}")
        for t in ("ydotool", "wtype", "xdotool", "wl-copy", "wl-paste",
                  "xclip", "pbcopy", "osascript"):
            p = shutil.which(t)
            if p:
                print(f"  found  : {t} -> {p}")
        if args.text is None:
            return

    ok, note = insert(args.text, args.mode)
    print(f"{'ok' if ok else 'failed'}: {note}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
