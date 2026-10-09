"""SQLite storage for campaigns (« lots ») and their queued messages (same database file as the contacts).

Each message keeps the values of its variables, and each campaign keeps its template and the variables it uses, so the
text of the messages not sent yet can be corrected later (`update_batch_template`)."""
import json
from dataclasses import dataclass, field

from common.app_paths import default_database_path
from common.blacklist_table import CREATE_BLACKLIST_TABLE_SQL, DELETE_PENDING_BLACKLISTED_MESSAGES_SQL
from common.conversation_table import (
    CREATE_CONVERSATIONS_TABLE_SQL,
    add_conversation_id_column,
    find_or_create_conversation,
)
from common.database import DatabaseTransaction
from common.message_template import find_used_variables
from common.sms_text import normalize_senegal_number
from outbox.batch_editing import EditableMessage, plan_batch_edit

WAITING = "WAITING"
SENDING = "SENDING"
SENT = "SENT"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"
DELETE_CHUNK_SIZE = 500  # stays under SQLite's variable limit
ALL_STATES = (WAITING, SENDING, SENT, FAILED, UNKNOWN)
# The Outbox shows the current run: outgoing messages plus the ones already SENT in this run (progress at a glance).
# Those SENT messages move to the Historique when the run ends, is stopped, or the queue is cleared (`archive_sent_messages`).
OUTBOX_STATES = (WAITING, SENDING, SENT, FAILED, UNKNOWN)
# Replies typed in the Inbox jump the queue: the sender takes them before the next campaign message.
NORMAL_PRIORITY, REPLY_PRIORITY = 0, 1
REPLY_LIST_NAME = "Réponse"

CREATE_CAMPAIGNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS campaigns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    message_template TEXT NOT NULL,
    list_names       TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    variables_used   TEXT NOT NULL DEFAULT '[]',
    simplify_to_7_bit INTEGER NOT NULL DEFAULT 0,
    is_reply         INTEGER NOT NULL DEFAULT 0,
    edited_at        TEXT
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
    updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    in_outbox      INTEGER NOT NULL DEFAULT 1,
    priority       INTEGER NOT NULL DEFAULT 0,
    variable_values TEXT NOT NULL DEFAULT '{}'
)
"""
CREATE_STATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS index_messages_state ON messages(state)"
EDITABLE_STATES = (WAITING, FAILED)


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
class BatchSummary:
    """One campaign as the Outbox lists it: its template, the variables it uses and how its messages are doing."""
    campaign_id: int
    created_at: str
    list_names: str
    message_template: str
    variables_used: tuple
    simplify_to_7_bit: bool
    edited_at: str
    state_counts: dict = field(default_factory=dict)

    @property
    def total_count(self):
        return sum(self.state_counts.values())

    @property
    def editable_count(self):
        """Messages whose text can still change: not sent yet, and a failed one is sent again by the next run."""
        return self.state_counts.get(WAITING, 0) + self.state_counts.get(FAILED, 0)


@dataclass
class BatchEditResult:
    updated_count: int          # messages whose text really changed
    legacy_count: int           # queued before variables were kept: left as they were


class BatchEditRejected(Exception):
    """The new text cannot be applied (unknown variable, empty or too long message...). The message is French."""


@dataclass
class MessageToSend:
    message_id: int
    phone_number: str
    message_text: str
    priority: int = NORMAL_PRIORITY


class OutboxStore:
    def __init__(self, database_path=None):
        self.database_path = database_path or default_database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute(CREATE_CAMPAIGNS_TABLE_SQL)
            connection.execute(CREATE_MESSAGES_TABLE_SQL)
            connection.execute(CREATE_STATE_INDEX_SQL)
            existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
            if "in_outbox" not in existing_columns:  # older database: every message already SENT belongs to the Historique
                connection.execute("ALTER TABLE messages ADD COLUMN in_outbox INTEGER NOT NULL DEFAULT 1")
                connection.execute("UPDATE messages SET in_outbox = 0 WHERE state = ?", (SENT,))
            if "priority" not in existing_columns:  # older database: every existing message is a normal one
                connection.execute("ALTER TABLE messages ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
            if "variable_values" not in existing_columns:  # older messages cannot be rebuilt: they keep their final text only
                connection.execute("ALTER TABLE messages ADD COLUMN variable_values TEXT NOT NULL DEFAULT '{}'")
            self.upgrade_campaigns_table(connection)
            connection.execute(CREATE_CONVERSATIONS_TABLE_SQL)
            connection.execute(CREATE_BLACKLIST_TABLE_SQL)
            add_conversation_id_column(connection, "messages")

    @staticmethod
    def upgrade_campaigns_table(connection):
        campaign_columns = {row[1] for row in connection.execute("PRAGMA table_info(campaigns)")}
        for column_name, column_definition in (
            ("variables_used", "TEXT NOT NULL DEFAULT '[]'"),
            ("simplify_to_7_bit", "INTEGER NOT NULL DEFAULT 0"),
            ("is_reply", "INTEGER NOT NULL DEFAULT 0"),
            ("edited_at", "TEXT"),
        ):
            if column_name not in campaign_columns:
                connection.execute(f"ALTER TABLE campaigns ADD COLUMN {column_name} {column_definition}")
                if column_name == "variables_used":
                    for campaign_id, message_template in connection.execute("SELECT id, message_template FROM campaigns").fetchall():
                        connection.execute(
                            "UPDATE campaigns SET variables_used = ? WHERE id = ?",
                            (json.dumps(sorted(find_used_variables(message_template))), campaign_id),
                        )
                if column_name == "is_reply":
                    connection.execute("UPDATE campaigns SET is_reply = 1 WHERE list_names = ?", (REPLY_LIST_NAME,))

    def queue_campaign(self, message_template, list_names, draft_messages, simplify_to_7_bit=False):
        """Save the campaign and all its messages in one transaction, every message WAITING. Each message keeps its
        variable values (`variable_values` on the draft message) so the batch can be corrected later. Returns the campaign id."""
        with DatabaseTransaction(self.database_path) as connection:
            blacklisted_numbers = {row[0] for row in connection.execute("SELECT phone_number FROM blacklisted_numbers")}
            draft_messages = [  # blacklisted numbers are never queued
                message for message in draft_messages
                if (normalize_senegal_number(message.phone_number) or message.phone_number) not in blacklisted_numbers
            ]
            campaign_id = connection.execute(
                "INSERT INTO campaigns (message_template, list_names, variables_used, simplify_to_7_bit) VALUES (?, ?, ?, ?)",
                (message_template, " + ".join(list_names), json.dumps(sorted(find_used_variables(message_template))), int(simplify_to_7_bit)),
            ).lastrowid
            connection.executemany(
                "INSERT INTO messages (campaign_id, list_name, recipient_name, phone_number, message_text, state, variable_values, "
                "conversation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (campaign_id, message.list_name, message.recipient_name, message.phone_number, message.message_text, WAITING,
                     json.dumps(getattr(message, "variable_values", None) or {}, ensure_ascii=False),
                     find_or_create_conversation(connection, message.phone_number, message.recipient_name))
                    for message in draft_messages
                ],
            )
        return campaign_id

    def queue_reply(self, phone_number, recipient_name, message_text):
        """Queue one reply with priority (its own one-message campaign). Returns the message id."""
        with DatabaseTransaction(self.database_path) as connection:
            campaign_id = connection.execute(
                "INSERT INTO campaigns (message_template, list_names, is_reply) VALUES (?, ?, 1)", (message_text, REPLY_LIST_NAME)
            ).lastrowid
            return connection.execute(
                "INSERT INTO messages (campaign_id, list_name, recipient_name, phone_number, message_text, state, priority, "
                "conversation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (campaign_id, REPLY_LIST_NAME, recipient_name, phone_number, message_text, WAITING, REPLY_PRIORITY,
                 find_or_create_conversation(connection, phone_number, recipient_name)),
            ).lastrowid

    # ---- batches (campaigns) ---------------------------------------------------------------------

    def list_batches(self):
        """The campaigns that still have messages in the Outbox (replies excluded), newest first."""
        with DatabaseTransaction(self.database_path) as connection:
            campaign_rows = connection.execute(
                "SELECT id, created_at, list_names, message_template, variables_used, simplify_to_7_bit, edited_at FROM campaigns "
                "WHERE is_reply = 0 AND id IN (SELECT campaign_id FROM messages WHERE in_outbox = 1) ORDER BY id DESC"
            ).fetchall()
            state_counts_by_campaign = {}
            for campaign_id, state, state_count in connection.execute(
                "SELECT campaign_id, state, COUNT(*) FROM messages GROUP BY campaign_id, state"
            ):
                state_counts_by_campaign.setdefault(campaign_id, {})[state] = state_count
        return [
            BatchSummary(
                campaign_id, created_at, list_names, message_template, tuple(json.loads(variables_used)), bool(simplify_to_7_bit),
                edited_at or "", state_counts_by_campaign.get(campaign_id, {}),
            )
            for campaign_id, created_at, list_names, message_template, variables_used, simplify_to_7_bit, edited_at in campaign_rows
        ]

    def get_batch(self, campaign_id):
        return next((batch for batch in self.list_batches() if batch.campaign_id == campaign_id), None)

    def list_editable_messages(self, campaign_id):
        """The messages of the batch whose text can still change, with the variable values to rebuild them from."""
        placeholders = ",".join("?" * len(EDITABLE_STATES))
        with DatabaseTransaction(self.database_path) as connection:
            rows = connection.execute(
                f"SELECT id, variable_values FROM messages WHERE campaign_id = ? AND state IN ({placeholders}) ORDER BY id",
                (campaign_id, *EDITABLE_STATES),
            ).fetchall()
        return [EditableMessage(message_id, json.loads(variable_values)) for message_id, variable_values in rows]

    def update_batch_template(self, campaign_id, new_template, simplify_to_7_bit=False):
        """Rebuild the text of every message of the batch that has not been sent (WAITING, or FAILED which the next run sends
        again) from its stored variables. SENT, SENDING and UNKNOWN messages never change. All or nothing: one transaction.
        Raises BatchEditRejected with a French reason when the new text cannot be applied."""
        placeholders = ",".join("?" * len(EDITABLE_STATES))
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")  # no message can be claimed for sending while the texts are rewritten
            if connection.execute("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,)).fetchone() is None:
                raise BatchEditRejected("Ce lot n'existe plus.")
            editable_rows = connection.execute(
                f"SELECT id, variable_values FROM messages WHERE campaign_id = ? AND state IN ({placeholders})",
                (campaign_id, *EDITABLE_STATES),
            ).fetchall()
            if not editable_rows:
                raise BatchEditRejected("Aucun message de ce lot n'est en attente : rien à modifier.")
            batch_edit_plan = plan_batch_edit(
                new_template,
                [EditableMessage(message_id, json.loads(variable_values)) for message_id, variable_values in editable_rows],
                simplify_to_7_bit,
            )
            if batch_edit_plan.rejection_reason:
                raise BatchEditRejected(batch_edit_plan.rejection_reason)
            updated_count = 0
            for message_id, new_message_text in batch_edit_plan.new_text_by_message_id.items():
                updated_count += connection.execute(
                    f"UPDATE messages SET message_text = ?, updated_at = CURRENT_TIMESTAMP "
                    f"WHERE id = ? AND state IN ({placeholders}) AND message_text <> ?",
                    (new_message_text, message_id, *EDITABLE_STATES, new_message_text),
                ).rowcount
            connection.execute(
                "UPDATE campaigns SET message_template = ?, variables_used = ?, simplify_to_7_bit = ?, edited_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (new_template, json.dumps(sorted(find_used_variables(new_template))), int(simplify_to_7_bit), campaign_id),
            )
        return BatchEditResult(updated_count, batch_edit_plan.legacy_count)

    def delete_batch_pending_messages(self, campaign_id):
        """Remove the batch's messages that have not been sent (WAITING, FAILED). What was sent stays in the Historique, and a
        message being sent or not confirmed (UNKNOWN) is left alone. The batch disappears once nothing is left of it."""
        placeholders = ",".join("?" * len(EDITABLE_STATES))
        with DatabaseTransaction(self.database_path) as connection:
            removed_count = connection.execute(
                f"DELETE FROM messages WHERE campaign_id = ? AND state IN ({placeholders})", (campaign_id, *EDITABLE_STATES)
            ).rowcount
            connection.execute("DELETE FROM campaigns WHERE id NOT IN (SELECT DISTINCT campaign_id FROM messages)")
        return removed_count

    def remove_pending_messages_for_blacklisted_numbers(self):
        """A number was just blacklisted: its campaign messages not sent yet (WAITING, FAILED) leave the queue. Replies you
        typed yourself stay. Returns how many were removed."""
        with DatabaseTransaction(self.database_path) as connection:
            removed_count = connection.execute(DELETE_PENDING_BLACKLISTED_MESSAGES_SQL).rowcount
            connection.execute("DELETE FROM campaigns WHERE id NOT IN (SELECT DISTINCT campaign_id FROM messages)")
        return removed_count

    def count_waiting_replies(self):
        with DatabaseTransaction(self.database_path) as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM messages WHERE state = ? AND priority = ?", (WAITING, REPLY_PRIORITY)
            ).fetchone()[0]

    def list_messages(self, states=ALL_STATES):
        """Messages in any of the given states in sending order: replies first, then oldest first."""
        placeholders = ",".join("?" * len(states))
        with DatabaseTransaction(self.database_path) as connection:
            rows = connection.execute(
                "SELECT id, state, recipient_name, phone_number, message_text, list_name, created_at, error_detail, updated_at "
                f"FROM messages WHERE state IN ({placeholders}) AND in_outbox = 1 ORDER BY priority DESC, id",
                tuple(states),
            ).fetchall()
        return [QueuedMessage(*row) for row in rows]

    def list_sent_messages(self):
        """The Historique: delivered messages, most recently sent first."""
        with DatabaseTransaction(self.database_path) as connection:
            rows = connection.execute(
                "SELECT id, state, recipient_name, phone_number, message_text, list_name, created_at, error_detail, updated_at "
                "FROM messages WHERE state = ? AND in_outbox = 0 ORDER BY updated_at DESC, id DESC",
                (SENT,),
            ).fetchall()
        return [QueuedMessage(*row) for row in rows]

    def count_by_state(self):
        with DatabaseTransaction(self.database_path) as connection:
            counts = dict(connection.execute(
                "SELECT state, COUNT(*) FROM messages WHERE in_outbox = 1 GROUP BY state"
            ).fetchall())
        return {state: counts.get(state, 0) for state in ALL_STATES}

    def claim_next_waiting_message(self, replies_only=False):
        """Take the next WAITING message (replies first, then the oldest) and mark it SENDING (one transaction).
        None when nothing is waiting (or, with `replies_only`, no reply is waiting)."""
        minimum_priority = REPLY_PRIORITY if replies_only else NORMAL_PRIORITY
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute(DELETE_PENDING_BLACKLISTED_MESSAGES_SQL)  # blacklisted after queueing: skipped, never sent
            row = connection.execute(
                "SELECT id, phone_number, message_text, priority FROM messages "
                "WHERE state = ? AND priority >= ? ORDER BY priority DESC, id LIMIT 1",
                (WAITING, minimum_priority),
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

    def requeue_failed_messages(self):
        """FAILED (certainly refused) and UNKNOWN (« ? », outcome unclear) messages go back to WAITING and, having the
        oldest ids, are sent first. Retrying an UNKNOWN one can duplicate it if it was in fact delivered: this is the
        owner's explicit choice, and it only happens when the owner presses Envoyer/Reprendre. Returns how many."""
        with DatabaseTransaction(self.database_path) as connection:
            return connection.execute(
                "UPDATE messages SET state = ?, error_detail = '', updated_at = CURRENT_TIMESTAMP WHERE state IN (?, ?)",
                (WAITING, FAILED, UNKNOWN),
            ).rowcount

    def archive_sent_messages(self):
        """Move the messages SENT during the run that just ended from the Outbox to the Historique."""
        with DatabaseTransaction(self.database_path) as connection:
            return connection.execute(
                "UPDATE messages SET in_outbox = 0 WHERE state = ? AND in_outbox = 1", (SENT,)
            ).rowcount

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
