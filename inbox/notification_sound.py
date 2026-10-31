"""Short sound for a newly received SMS (the system's notification sound, non-blocking)."""
from common.sound_player import play_system_notification_sound


def play_new_message_sound():
    play_system_notification_sound()
