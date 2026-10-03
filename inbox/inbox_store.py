"""SQLite storage for received SMS (same database file as the contacts and the outbox)."""
from dataclasses import dataclass

from common.app_paths import default_database_path
from common.database import DatabaseTransaction
from common.sms_text import normalize_senegal_number

DELETE_CHUNK_SIZE = 500

CREATE_RECEIVED_MESSAGES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS received_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_number TEXT NOT NULL,
    message_text  TEXT NOT NULL,
    received_at   TEXT NOT NULL,
    is_read       INTEGER NOT NULL DEFAULT 0,
    stored_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""
# a message that was stored but could not be deleted from the SIM comes back on the next read: ignore the copy
CREATE_DUPLICATE_GUARD_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS index_received_messages_unique "
    "ON received_messages(sender_number, received_at, message_text)"
)


@dataclass
class InboxMessage:
    message_id: int
    sender_number: str
    sender_name: str
    message_text: str
    received_at: str
    is_read: bool


class InboxStore:
    """Short-lived connection per call, so the dongle thread and the UI thread can both use it."""

    def __init__(self, database_path=None):
        self.database_path = database_path or default_database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute(CREATE_RECEIVED_MESSAGES_TABLE_SQL)
            connection.execute(CREATE_DUPLICATE_GUARD_INDEX_SQL)

    def store_received_messages(self, received_messages):
        """Save the messages in one transaction. Returns how many were new (copies already stored are ignored).
        Raises if the save fails, so the caller must not delete anything from the SIM."""
        with DatabaseTransaction(self.database_path) as connection:
            inserted_count = 0
            for received_message in received_messages:
                inserted_count += connection.execute(
                    "INSERT OR IGNORE INTO received_messages (sender_number, message_text, received_at) VALUES (?, ?, ?)",
                    (received_message.sender_number, received_message.message_text, received_message.received_at),
                ).rowcount
        return inserted_count

    def list_messages(self):
        """Newest first. The sender name comes from the contacts when the number matches one
        (the operator may send '221771234567' or '77 123 45 67', so numbers are normalized before comparing)."""
        with DatabaseTransaction(self.database_path) as connection:
            has_contacts_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'contacts'"
            ).fetchone()
            contact_name_by_number = {}
            if has_contacts_table:
                for phone_number, contact_name in connection.execute(
                    "SELECT phone_number, name FROM contacts WHERE name <> '' ORDER BY id DESC"
                ):
                    contact_name_by_number[normalize_senegal_number(phone_number) or phone_number] = contact_name
            rows = connection.execute(
                "SELECT id, sender_number, message_text, received_at, is_read "
                "FROM received_messages ORDER BY received_at DESC, id DESC"
            ).fetchall()
        return [
            InboxMessage(
                message_id,
                sender_number,
                contact_name_by_number.get(normalize_senegal_number(sender_number) or sender_number, ""),
                message_text,
                received_at,
                bool(is_read),
            )
            for message_id, sender_number, message_text, received_at, is_read in rows
        ]

    def count_unread(self):
        with DatabaseTransaction(self.database_path) as connection:
            return connection.execute("SELECT COUNT(*) FROM received_messages WHERE is_read = 0").fetchone()[0]

    def mark_as_read(self, message_ids):
        with DatabaseTransaction(self.database_path) as connection:
            for start in range(0, len(message_ids), DELETE_CHUNK_SIZE):
                chunk = message_ids[start:start + DELETE_CHUNK_SIZE]
                placeholders = ",".join("?" * len(chunk))
                connection.execute(f"UPDATE received_messages SET is_read = 1 WHERE id IN ({placeholders})", chunk)

    def mark_all_as_read(self):
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute("UPDATE received_messages SET is_read = 1 WHERE is_read = 0")

    def delete_messages(self, message_ids):
        with DatabaseTransaction(self.database_path) as connection:
            for start in range(0, len(message_ids), DELETE_CHUNK_SIZE):
                chunk = message_ids[start:start + DELETE_CHUNK_SIZE]
                placeholders = ",".join("?" * len(chunk))
                connection.execute(f"DELETE FROM received_messages WHERE id IN ({placeholders})", chunk)
