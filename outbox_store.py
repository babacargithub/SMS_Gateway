"""SQLite storage for campaigns and their queued messages (same database file as the contacts)."""
from dataclasses import dataclass

from contact_store import DatabaseTransaction, default_database_path

WAITING = "WAITING"
SENDING = "SENDING"
SENT = "SENT"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"
DELETE_CHUNK_SIZE = 500  # stays under SQLite's variable limit
ALL_STATES = (WAITING, SENDING, SENT, FAILED, UNKNOWN)
OUTBOX_STATES = (WAITING, SENDING, FAILED, UNKNOWN)  # the Outbox only shows outgoing messages; SENT ones live in the Historique

CREATE_CAMPAIGNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS campaigns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    message_template TEXT NOT NULL,
    list_names       TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

# recipient, number and text are copied into the message, so deleting a contact or a list never alters the Outbox
CREATE_MESSAGES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id    INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    list_name      TEXT NOT NULL DEFAULT '',
    recipient_name TEXT NOT NULL DEFAULT '',
    phone_number   TEXT NOT NULL,
    message_text   TEXT NOT NULL,
    state          TEXT NOT NULL DEFAULT 'WAITING',
    error_detail   TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""
CREATE_STATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS index_messages_state ON messages(state)"


@dataclass
class QueuedMessage:
    message_id: int
    state: str
    recipient_name: str
    phone_number: str
    message_text: str
    list_name: str
    created_at: str
    error_detail: str
    updated_at: str = ""  # for a SENT message: when it was delivered


@dataclass
class MessageToSend:
    message_id: int
    phone_number: str
    message_text: str


class OutboxStore:
    def __init__(self, database_path=None):
        self.database_path = database_path or default_database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute(CREATE_CAMPAIGNS_TABLE_SQL)
            connection.execute(CREATE_MESSAGES_TABLE_SQL)
            connection.execute(CREATE_STATE_INDEX_SQL)

    def queue_campaign(self, message_template, list_names, draft_messages):
        """Save the campaign and all its messages in one transaction, every message WAITING. Returns the campaign id."""
        with DatabaseTransaction(self.database_path) as connection:
            campaign_id = connection.execute(
                "INSERT INTO campaigns (message_template, list_names) VALUES (?, ?)",
                (message_template, " + ".join(list_names)),
            ).lastrowid
            connection.executemany(
                "INSERT INTO messages (campaign_id, list_name, recipient_name, phone_number, message_text, state) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (campaign_id, message.list_name, message.recipient_name, message.phone_number, message.message_text, WAITING)
                    for message in draft_messages
                ],
            )
        return campaign_id

    def list_messages(self, states=ALL_STATES):
        """Messages in any of the given states, oldest first."""
        placeholders = ",".join("?" * len(states))
        with DatabaseTransaction(self.database_path) as connection:
            rows = connection.execute(
                "SELECT id, state, recipient_name, phone_number, message_text, list_name, created_at, error_detail, updated_at "
                f"FROM messages WHERE state IN ({placeholders}) ORDER BY id",
                tuple(states),
            ).fetchall()
        return [QueuedMessage(*row) for row in rows]

    def list_sent_messages(self):
        """The Historique: delivered messages, most recently sent first."""
        with DatabaseTransaction(self.database_path) as connection:
            rows = connection.execute(
                "SELECT id, state, recipient_name, phone_number, message_text, list_name, created_at, error_detail, updated_at "
                "FROM messages WHERE state = ? ORDER BY updated_at DESC, id DESC",
                (SENT,),
            ).fetchall()
        return [QueuedMessage(*row) for row in rows]

    def count_by_state(self):
        with DatabaseTransaction(self.database_path) as connection:
            counts = dict(connection.execute("SELECT state, COUNT(*) FROM messages GROUP BY state").fetchall())
        return {state: counts.get(state, 0) for state in ALL_STATES}

    def claim_next_waiting_message(self):
        """Take the oldest WAITING message and mark it SENDING (one transaction). None when nothing is waiting."""
        with DatabaseTransaction(self.database_path) as connection:
            row = connection.execute(
                "SELECT id, phone_number, message_text FROM messages WHERE state = ? ORDER BY id LIMIT 1", (WAITING,)
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE messages SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (SENDING, row[0])
            )
        return MessageToSend(*row)

    def return_message_to_waiting(self, message_id):
        """Only for a message that was certainly never handed to a dongle (e.g. no dongle ready)."""
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute(
                "UPDATE messages SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND state = ?",
                (WAITING, message_id, SENDING),
            )

    def finish_message(self, message_id, final_state, error_detail=""):
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute(
                "UPDATE messages SET state = ?, error_detail = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (final_state, error_detail, message_id),
            )

    def recover_interrupted_sends(self):
        """Messages still SENDING when the app starts were cut off mid-send: nobody knows whether they went out,
        so they become UNKNOWN and are never resent automatically (no duplicates). Returns how many."""
        with DatabaseTransaction(self.database_path) as connection:
            return connection.execute(
                "UPDATE messages SET state = ?, error_detail = ?, updated_at = CURRENT_TIMESTAMP WHERE state = ?",
                (UNKNOWN, "L'application a été fermée pendant l'envoi", SENDING),
            ).rowcount

    def delete_waiting_messages(self):
        """Remove the messages that have not been sent yet. Returns how many were removed."""
        with DatabaseTransaction(self.database_path) as connection:
            removed_count = connection.execute("DELETE FROM messages WHERE state = ?", (WAITING,)).rowcount
            connection.execute("DELETE FROM campaigns WHERE id NOT IN (SELECT DISTINCT campaign_id FROM messages)")
        return removed_count

    def delete_messages(self, message_ids):
        """Remove the chosen messages from the queue. A message being sent right now (SENDING) is never removed:
        its outcome still has to be recorded. Returns how many were removed."""
        removed_count = 0
        with DatabaseTransaction(self.database_path) as connection:
            for start in range(0, len(message_ids), DELETE_CHUNK_SIZE):
                chunk = message_ids[start:start + DELETE_CHUNK_SIZE]
                placeholders = ",".join("?" * len(chunk))
                removed_count += connection.execute(
                    f"DELETE FROM messages WHERE state <> ? AND id IN ({placeholders})", [SENDING, *chunk]
                ).rowcount
            connection.execute("DELETE FROM campaigns WHERE id NOT IN (SELECT DISTINCT campaign_id FROM messages)")
        return removed_count
