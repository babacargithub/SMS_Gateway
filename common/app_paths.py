"""Where the app keeps its files (outside the project, so a git checkout never touches user data)."""
from pathlib import Path

APPLICATION_SUPPORT_DIRECTORY = Path.home() / "Library" / "Application Support" / "SMS Gateway"


def default_database_path():
    return APPLICATION_SUPPORT_DIRECTORY / "sms_gateway.db"
