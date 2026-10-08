"""SQLite storage for contact lists and their contacts.

Opens a short-lived connection per call, so any thread can use it.
Deleting a list deletes its contacts (ON DELETE CASCADE)."""
import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from common.app_paths import default_database_path
from common.database import DatabaseTransaction

DELETE_CHUNK_SIZE = 500  # stays under SQLite's variable limit
LEGACY_LIST_NAME = "Imported contacts"

CREATE_LISTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS contact_lists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

CREATE_CONTACTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS contacts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id       INTEGER NOT NULL REFERENCES contact_lists(id) ON DELETE CASCADE,
    name          TEXT NOT NULL DEFAULT '',
    phone_number  TEXT NOT NULL,
    custom_fields TEXT NOT NULL DEFAULT '{}',
    column_values TEXT NOT NULL DEFAULT '{}',
    source_file   TEXT NOT NULL DEFAULT '',
    imported_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (list_id, phone_number)
)
"""


class ListNameAlreadyExists(Exception):
    pass


@dataclass
class ContactList:
    list_id: int
    name: str
    contact_count: int
    created_date: str


@dataclass
class StoredContact:
    contact_id: int
    name: str
    phone_number: str
    custom_fields: dict
    column_values: dict = field(default_factory=dict)   # "col1" -> value, the {col1} … {col10} variables


class ContactStore:
    def __init__(self, database_path=None):
        self.database_path = Path(database_path or default_database_path())
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_contacts_without_lists_if_needed()
        with self._connect() as connection:
            connection.execute(CREATE_LISTS_TABLE_SQL)
            connection.execute(CREATE_CONTACTS_TABLE_SQL)
            contact_columns = [row[1] for row in connection.execute("PRAGMA table_info(contacts)")]
            if "column_values" not in contact_columns:   # database created before {col1} … {col10} existed
                connection.execute("ALTER TABLE contacts ADD COLUMN column_values TEXT NOT NULL DEFAULT '{}'")

    def _connect(self):
        return DatabaseTransaction(self.database_path)

    def _migrate_contacts_without_lists_if_needed(self):
        """Earlier versions stored contacts without lists: move them into one list, keeping a backup file."""
        with self._connect() as connection:
            existing_columns = [row[1] for row in connection.execute("PRAGMA table_info(contacts)")]
        if not existing_columns or "list_id" in existing_columns:
            return
        shutil.copy2(self.database_path, self.database_path.with_name(self.database_path.name + ".before-lists.bak"))
        with self._connect() as connection:
            connection.execute("BEGIN")
            connection.execute("ALTER TABLE contacts RENAME TO contacts_before_lists")
            connection.execute(CREATE_LISTS_TABLE_SQL)
            connection.execute(CREATE_CONTACTS_TABLE_SQL)
            legacy_contact_count = connection.execute("SELECT COUNT(*) FROM contacts_before_lists").fetchone()[0]
            if legacy_contact_count:
                legacy_list_id = connection.execute(
                    "INSERT INTO contact_lists (name) VALUES (?)", (LEGACY_LIST_NAME,)
                ).lastrowid
                connection.execute(
                    "INSERT INTO contacts (id, list_id, name, phone_number, custom_fields, source_file, imported_at) "
                    "SELECT id, ?, name, phone_number, custom_fields, source_file, imported_at FROM contacts_before_lists",
                    (legacy_list_id,),
                )
            connection.execute("DROP TABLE contacts_before_lists")

    # ---- lists -------------------------------------------------------------------------------

    def list_contact_lists(self):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT contact_lists.id, contact_lists.name, COUNT(contacts.id), substr(contact_lists.created_at, 1, 10) "
                "FROM contact_lists LEFT JOIN contacts ON contacts.list_id = contact_lists.id "
                "GROUP BY contact_lists.id ORDER BY contact_lists.name COLLATE NOCASE"
            ).fetchall()
        return [ContactList(row[0], row[1], row[2], row[3]) for row in rows]

    @staticmethod
    def _find_list_id_by_name(connection, list_name):
        """Case-insensitive for all letters (SQLite's NOCASE only handles A-Z, not È/è)."""
        wanted_name = list_name.strip().casefold()
        for list_id, existing_name in connection.execute("SELECT id, name FROM contact_lists"):
            if existing_name.casefold() == wanted_name:
                return list_id
        return None

    def list_name_exists(self, list_name):
        with self._connect() as connection:
            return self._find_list_id_by_name(connection, list_name) is not None

    def rename_list(self, list_id, new_name):
        """Changing only the capitalization of the same list is allowed."""
        with self._connect() as connection:
            list_id_using_name = self._find_list_id_by_name(connection, new_name)
            if list_id_using_name not in (None, list_id):
                raise ListNameAlreadyExists(new_name)
            connection.execute("UPDATE contact_lists SET name = ? WHERE id = ?", (new_name.strip(), list_id))

    def delete_list(self, list_id):
        """Deletes the list and, by cascade, every contact in it."""
        with self._connect() as connection:
            connection.execute("DELETE FROM contact_lists WHERE id = ?", (list_id,))

    # ---- importing ---------------------------------------------------------------------------

    def import_into_new_list(self, list_name, parsed_contacts, source_file_name):
        """Create the list and add the contacts in one transaction (no empty list left behind on error).
        Returns (list_id, inserted_count, skipped_count)."""
        try:
            with self._connect() as connection:
                if self._find_list_id_by_name(connection, list_name) is not None:
                    raise ListNameAlreadyExists(list_name)
                new_list_id = connection.execute(
                    "INSERT INTO contact_lists (name) VALUES (?)", (list_name.strip(),)
                ).lastrowid
                inserted_count = self._insert_contacts(connection, new_list_id, parsed_contacts, source_file_name)
        except sqlite3.IntegrityError as integrity_error:
            raise ListNameAlreadyExists(list_name) from integrity_error
        return new_list_id, inserted_count, len(parsed_contacts) - inserted_count

    def import_into_existing_list(self, list_id, parsed_contacts, source_file_name):
        """Numbers already in that list are skipped. Returns (inserted_count, skipped_count)."""
        with self._connect() as connection:
            inserted_count = self._insert_contacts(connection, list_id, parsed_contacts, source_file_name)
        return inserted_count, len(parsed_contacts) - inserted_count

    @staticmethod
    def _insert_contacts(connection, list_id, parsed_contacts, source_file_name):
        changes_before = connection.total_changes
        connection.executemany(
            "INSERT OR IGNORE INTO contacts (list_id, name, phone_number, custom_fields, column_values, source_file) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (list_id, contact.name, contact.phone_number, json.dumps(contact.custom_fields, ensure_ascii=False),
                 json.dumps(getattr(contact, "column_values", {}), ensure_ascii=False), source_file_name)
                for contact in parsed_contacts
            ],
        )
        return connection.total_changes - changes_before

    # ---- contacts ----------------------------------------------------------------------------

    def list_contacts(self, list_id):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name, phone_number, custom_fields, column_values FROM contacts WHERE list_id = ? ORDER BY id",
                (list_id,),
            ).fetchall()
        return [StoredContact(row[0], row[1], row[2], json.loads(row[3]), json.loads(row[4])) for row in rows]

    def list_contacts_for_lists(self, list_ids):
        """Contacts of several lists, as [(list_name, StoredContact)], ordered by list name then import order."""
        if not list_ids:
            return []
        placeholders = ",".join("?" * len(list_ids))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT contact_lists.name, contacts.id, contacts.name, contacts.phone_number, contacts.custom_fields, contacts.column_values "
                "FROM contacts JOIN contact_lists ON contact_lists.id = contacts.list_id "
                f"WHERE contacts.list_id IN ({placeholders}) "
                "ORDER BY contact_lists.name COLLATE NOCASE, contacts.id",
                list(list_ids),
            ).fetchall()
        return [(row[0], StoredContact(row[1], row[2], row[3], json.loads(row[4]), json.loads(row[5]))) for row in rows]

    def delete_contacts(self, contact_ids):
        with self._connect() as connection:
            for chunk_start in range(0, len(contact_ids), DELETE_CHUNK_SIZE):
                id_chunk = contact_ids[chunk_start:chunk_start + DELETE_CHUNK_SIZE]
                placeholders = ",".join("?" * len(id_chunk))
                connection.execute(f"DELETE FROM contacts WHERE id IN ({placeholders})", id_chunk)
