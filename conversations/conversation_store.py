"""Reads a conversation (sent + received messages of one correspondent, oldest first) and groups the messages that
existed before conversations did. Same database file as the inbox and the outbox."""
import sqlite3
from dataclasses import dataclass

from common.app_paths import default_database_path
from common.conversation_table import (
    CREATE_CONVERSATIONS_TABLE_SQL,
    add_conversation_id_column,
    conversation_key_for,
    link_unlinked_messages,
)
from common.database import DatabaseTransaction

SENT_DIRECTION = "SENT"
RECEIVED_DIRECTION = "RECEIVED"


@dataclass
class ConversationEntry:
    direction: str          # SENT_DIRECTION or RECEIVED_DIRECTION
    entry_id: int           # id in `messages` (sent) or `received_messages` (received)
    message_text: str
    occurred_at: str        # received_at, or for a sent message the moment it was delivered (queued time while not sent yet)
    outbox_state: str = ""  # sent messages only: WAITING, SENDING, SENT, FAILED, UNKNOWN
    error_detail: str = ""


@dataclass
class Conversation:
    conversation_key: str   # '+221771234567', or the raw sender name (« Expresso »)
    contact_name: str
    entries: list


class ConversationStore:
    """Short-lived connection per call, like the other stores. Creating it runs the one-time grouping of old messages."""

    def __init__(self, database_path=None):
        self.database_path = database_path or default_database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute(CREATE_CONVERSATIONS_TABLE_SQL)
            self.ensure_message_tables_are_linkable(connection)
        self.group_existing_messages_into_conversations()

    @staticmethod
    def ensure_message_tables_are_linkable(connection):
        """The inbox and outbox stores normally did this already; harmless when the tables do not exist yet."""
        for table_name in ("messages", "received_messages"):
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)).fetchone():
                add_conversation_id_column(connection, table_name)

    def group_existing_messages_into_conversations(self):
        """One-time refactor (idempotent: only messages without a conversation are touched, so later starts do nothing).
        Writes a `.before-conversations.bak` copy of the database first when there is something to group.
        Returns how many messages were linked."""
        if self.count_messages_without_conversation() == 0:
            return 0
        self.write_backup_once()
        with DatabaseTransaction(self.database_path) as connection:
            linked_count = 0
            if self.table_exists(connection, "messages"):
                linked_count += link_unlinked_messages(connection, "messages", "phone_number", "recipient_name")
            if self.table_exists(connection, "received_messages"):
                linked_count += link_unlinked_messages(connection, "received_messages", "sender_number")
        return linked_count

    @staticmethod
    def table_exists(connection, table_name):
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
        ).fetchone() is not None

    def count_messages_without_conversation(self):
        with DatabaseTransaction(self.database_path) as connection:
            return sum(
                connection.execute(f"SELECT COUNT(*) FROM {table_name} WHERE conversation_id IS NULL").fetchone()[0]
                for table_name in ("messages", "received_messages")
                if self.table_exists(connection, table_name)
            )

    def write_backup_once(self):
        backup_path = self.database_path.with_name(self.database_path.name + ".before-conversations.bak")
        if backup_path.exists():
            return
        source_connection = sqlite3.connect(self.database_path)
        backup_connection = sqlite3.connect(backup_path)
        try:
            source_connection.backup(backup_connection)
        finally:
            backup_connection.close()
            source_connection.close()

    def load_conversation(self, raw_number_text):
        """The conversation with this number, oldest message first. Empty (no entries) when nothing was exchanged yet."""
        conversation_key = conversation_key_for(raw_number_text)
        with DatabaseTransaction(self.database_path) as connection:
            conversation_row = connection.execute(
                "SELECT id, contact_name FROM conversations WHERE conversation_key = ?", (conversation_key,)
            ).fetchone()
            if conversation_row is None:
                return Conversation(conversation_key, self.find_contact_name(connection, conversation_key), [])
            conversation_id, remembered_contact_name = conversation_row
            sent_rows = connection.execute(
                "SELECT id, message_text, CASE WHEN state = 'SENT' THEN updated_at ELSE created_at END, state, error_detail "
                "FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchall()
            received_rows = connection.execute(
                "SELECT id, message_text, received_at FROM received_messages WHERE conversation_id = ?", (conversation_id,)
            ).fetchall()
            contact_name = self.find_contact_name(connection, conversation_key) or remembered_contact_name
        entries = [ConversationEntry(SENT_DIRECTION, *row) for row in sent_rows]
        entries += [ConversationEntry(RECEIVED_DIRECTION, *row) for row in received_rows]
        entries.sort(key=lambda entry: (entry.occurred_at, entry.direction == SENT_DIRECTION, entry.entry_id))
        return Conversation(conversation_key, contact_name, entries)

    def find_contact_name(self, connection, conversation_key):
        if not self.table_exists(connection, "contacts"):
            return ""
        contact_row = connection.execute(
            "SELECT name FROM contacts WHERE phone_number = ? AND name <> '' ORDER BY id DESC LIMIT 1", (conversation_key,)
        ).fetchone()
        return contact_row[0] if contact_row else ""

    def mark_received_messages_as_read(self, raw_number_text):
        """Opening a conversation reads everything received in it."""
        conversation_key = conversation_key_for(raw_number_text)
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute(
                "UPDATE received_messages SET is_read = 1 WHERE is_read = 0 AND conversation_id IN "
                "(SELECT id FROM conversations WHERE conversation_key = ?)",
                (conversation_key,),
            )
