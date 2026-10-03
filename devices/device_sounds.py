"""Short sounds when a dongle is plugged in or unplugged (bundled files, played without blocking)."""
import subprocess
from pathlib import Path

from PySide6.QtWidgets import QApplication

SOUNDS_FOLDER = Path(__file__).parent / "sounds"
DEVICE_CONNECTED_SOUND_PATH = SOUNDS_FOLDER / "device_connected.wav"  # rising two-tone
DEVICE_DISCONNECTED_SOUND_PATH = SOUNDS_FOLDER / "device_disconnected.wav"  # falling two-tone


def _play_sound_file(sound_path):
    try:
        subprocess.Popen(["afplay", str(sound_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        QApplication.beep()


def play_device_connected_sound():
    _play_sound_file(DEVICE_CONNECTED_SOUND_PATH)


def play_device_disconnected_sound():
    _play_sound_file(DEVICE_DISCONNECTED_SOUND_PATH)
