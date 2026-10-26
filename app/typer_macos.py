import subprocess
import time
from typing import Optional


def _osascript_focus(title: str) -> bool:
    escaped = title.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "System Events"
        set frontApp to first application process whose frontmost is true
        set windowList to every window of (every process whose name contains "{escaped}")
    end tell
    tell application "{escaped}" to activate
    '''
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        pass

    try:
        script2 = f'''
        tell application "System Events"
            set visibleProcesses to every process whose visible is true
            repeat with p in visibleProcesses
                try
                    set winTitle to name of front window of p
                    if winTitle contains "{escaped}" then
                        set frontmost of p to true
                        return true
                    end if
                end try
            end repeat
        end tell
        '''
        subprocess.run(
            ["osascript", "-e", script2],
            capture_output=True, timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        pass
    return False


def _osascript_type(text: str) -> bool:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "System Events"
        keystroke "{escaped}"
    end tell
    '''
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        pass

    try:
        import pyautogui
        pyautogui.write(text, interval=0.01)
        return True
    except ImportError:
        pass
    return False


def _get_active_window_title_macos() -> Optional[str]:
    script = '''
    tell application "System Events"
        set frontApp to first application process whose frontmost is true
        set appName to name of frontApp
        try
            set winTitle to name of front window of frontApp
            return appName & " - " & winTitle
        end try
        return appName
    end tell
    '''
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        pass
    return None


def insert_text_macos(text: str, target_window_title: str = "") -> bool:
    if not text:
        return False

    if target_window_title:
        _osascript_focus(target_window_title)
        time.sleep(0.3)

    return _osascript_type(text)


def get_open_windows_macos() -> list[dict]:
    script = '''
    tell application "System Events"
        set output to ""
        set visibleProcesses to every process whose visible is true
        repeat with p in visibleProcesses
            try
                set appName to name of p
                set winList to every window of p
                repeat with w in winList
                    try
                        set winTitle to name of w
                        if winTitle is not "" then
                            set output to output & appName & "|||" & winTitle & "\n"
                        end if
                    end try
                end repeat
            end try
        end repeat
        return output
    end tell
    '''
    windows = []
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                if "|||" in line:
                    app_name, win_title = line.split("|||", 1)
                    windows.append({"title": f"{app_name} - {win_title}"})
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        pass
    return windows