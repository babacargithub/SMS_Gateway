"""SQLite helper shared by every store."""
import sqlite3


class DatabaseTransaction:
    """Context manager: commits on success, rolls back on error, always closes."""

    def __init__(self, database_path):
        self.connection = sqlite3.connect(database_path)
        self.connection.execute("PRAGMA foreign_keys = ON")  # needed for ON DELETE CASCADE

    def __enter__(self):
        return self.connection

    def __exit__(self, error_type, error, traceback):
        if error_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.connection.close()
        return False
