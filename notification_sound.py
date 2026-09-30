"""Short sound for a newly received SMS (macOS system sound, non-blocking)."""
import subprocess

from PySide6.QtWidgets import QApplication

NEW_MESSAGE_SOUND_PATH = "/System/Library/Sounds/Glass.aiff"


def play_new_message_sound():
    try:
        subprocess.Popen(["afplay", NEW_MESSAGE_SOUND_PATH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        QApplication.beep()
