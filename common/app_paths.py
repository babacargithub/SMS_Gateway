"""Where the app keeps its files (outside the project, so a git checkout never touches user data).

macOS: ~/Library/Application Support/SMS Gateway. Windows: %APPDATA%\\SMS Gateway (C:\\Users\\<name>\\AppData\\Roaming)."""
import os
from pathlib import Path

from common.platform_support import IS_WINDOWS


def _application_support_directory():
    if IS_WINDOWS:
        roaming_folder = os.environ.get("APPDATA")
        return (Path(roaming_folder) if roaming_folder else Path.home() / "AppData" / "Roaming") / "SMS Gateway"
    return Path.home() / "Library" / "Application Support" / "SMS Gateway"


APPLICATION_SUPPORT_DIRECTORY = _application_support_directory()


def default_database_path():
    return APPLICATION_SUPPORT_DIRECTORY / "sms_gateway.db"
