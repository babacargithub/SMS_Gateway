"""Shared SQL for conversations: one conversation per correspondent, holding the messages sent to and received from them.

`messages` (sent) and `received_messages` (received) each carry a `conversation_id`. Both stores use these helpers inside
their own transaction, so a message is always linked to its conversation in the same transaction that saves it."""
from common.sms_text import normalize_senegal_number

CREATE_CONVERSATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_key TEXT NOT NULL UNIQUE,
    contact_name   TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def conversation_key_for(raw_number_text):
    """'+221771234567' for any spelling of a Senegalese number; a sender such as « Expresso » is kept as it is."""
    stripped_number_text = raw_number_text.strip()
    return normalize_senegal_number(stripped_number_text) or stripped_number_text


def find_or_create_conversation(connection, raw_number_text, contact_name=""):
    """The id of this correspondent's conversation, created when there is none yet. A non-empty name is remembered."""
    conversation_key = conversation_key_for(raw_number_text)
    existing_row = connection.execute(
        "SELECT id, contact_name FROM conversations WHERE conversation_key = ?", (conversation_key,)
    ).fetchone()
    if existing_row is None:
        return connection.execute(
            "INSERT INTO conversations (conversation_key, contact_name) VALUES (?, ?)", (conversation_key, contact_name)
        ).lastrowid
    conversation_id, known_contact_name = existing_row
    if contact_name and not known_contact_name:
        connection.execute("UPDATE conversations SET contact_name = ? WHERE id = ?", (contact_name, conversation_id))
    return conversation_id


def add_conversation_id_column(connection, table_name):
    """Older databases: add the link column (left empty until the one-time grouping fills it)."""
    existing_columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}
    if "conversation_id" not in existing_columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN conversation_id INTEGER REFERENCES conversations(id)")
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS index_{table_name}_conversation ON {table_name}(conversation_id)"
    )


def link_unlinked_messages(connection, table_name, number_column, name_column=None):
    """Put every message that has no conversation yet into its correspondent's conversation. Returns how many were linked."""
    name_selection = name_column or "''"
    unlinked_rows = connection.execute(
        f"SELECT id, {number_column}, {name_selection} FROM {table_name} WHERE conversation_id IS NULL"
    ).fetchall()
    conversation_id_by_key = {}
    for message_id, raw_number_text, contact_name in unlinked_rows:
        conversation_key = conversation_key_for(raw_number_text)
        if conversation_key not in conversation_id_by_key:
            conversation_id_by_key[conversation_key] = find_or_create_conversation(connection, raw_number_text, contact_name)
        elif contact_name:
            find_or_create_conversation(connection, raw_number_text, contact_name)  # remembers the name if still unknown
        connection.execute(
            f"UPDATE {table_name} SET conversation_id = ? WHERE id = ?", (conversation_id_by_key[conversation_key], message_id)
        )
    return len(unlinked_rows)
