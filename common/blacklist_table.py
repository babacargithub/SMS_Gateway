"""Shared SQL for the blacklist: numbers that campaigns must never be sent to. The numbers are stored normalized
('+221' + 9 digits), exactly as the Outbox stores them, so a plain comparison in SQL is enough."""
CREATE_BLACKLIST_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS blacklisted_numbers (
    phone_number TEXT PRIMARY KEY,
    contact_name TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""
# campaign messages (priority 0) still queued for a blacklisted number; manual replies (priority 1) are never touched
DELETE_PENDING_BLACKLISTED_MESSAGES_SQL = (
    "DELETE FROM messages WHERE priority = 0 AND state IN ('WAITING', 'FAILED') "
    "AND phone_number IN (SELECT phone_number FROM blacklisted_numbers)"
)
