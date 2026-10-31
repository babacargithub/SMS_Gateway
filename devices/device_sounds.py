"""Short sounds when a dongle is plugged in or unplugged (bundled files, played without blocking)."""
from pathlib import Path

from common.sound_player import play_sound_file

SOUNDS_FOLDER = Path(__file__).parent / "sounds"
DEVICE_CONNECTED_SOUND_PATH = SOUNDS_FOLDER / "device_connected.wav"  # rising two-tone
DEVICE_DISCONNECTED_SOUND_PATH = SOUNDS_FOLDER / "device_disconnected.wav"  # falling two-tone


def play_device_connected_sound():
    play_sound_file(DEVICE_CONNECTED_SOUND_PATH)


def play_device_disconnected_sound():
    play_sound_file(DEVICE_DISCONNECTED_SOUND_PATH)
