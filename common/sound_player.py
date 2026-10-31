"""Play a short sound without blocking: `afplay` on macOS, `winsound` on Windows, a Qt beep if neither works."""
import subprocess

from PySide6.QtWidgets import QApplication

from common.platform_support import IS_WINDOWS

MACOS_NOTIFICATION_SOUND_PATH = "/System/Library/Sounds/Glass.aiff"


def play_sound_file(sound_path):
    """`sound_path` should be a .wav file (the only format both systems play)."""
    try:
        if IS_WINDOWS:
            import winsound
            winsound.PlaySound(str(sound_path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        else:
            subprocess.Popen(["afplay", str(sound_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, RuntimeError):
        QApplication.beep()


def play_system_notification_sound():
    """The system's own "something arrived" sound (Glass on macOS, the Asterisk/notification sound on Windows)."""
    try:
        if IS_WINDOWS:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        else:
            subprocess.Popen(["afplay", MACOS_NOTIFICATION_SOUND_PATH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, RuntimeError):
        QApplication.beep()
