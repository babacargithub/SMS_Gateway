"""Numbers on the blacklist: campaigns skip them. Same database file as everything else."""
from common.app_paths import default_database_path
from common.blacklist_table import CREATE_BLACKLIST_TABLE_SQL
from common.database import DatabaseTransaction
from common.sms_text import normalize_senegal_number


class BlacklistStore:
    """Short-lived connection per call. Only real Senegalese numbers can be blacklisted (an alphanumeric sender
    such as « Expresso » cannot be a campaign recipient anyway)."""

    def __init__(self, database_path=None):
        self.database_path = database_path or default_database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute(CREATE_BLACKLIST_TABLE_SQL)

    def add_number(self, raw_number_text, contact_name=""):
        """Returns False when the text is not a valid number. Adding a number already listed is fine."""
        normalized_number = normalize_senegal_number(raw_number_text)
        if normalized_number is None:
            return False
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO blacklisted_numbers (phone_number, contact_name) VALUES (?, ?)",
                (normalized_number, contact_name),
            )
        return True

    def remove_number(self, raw_number_text):
        normalized_number = normalize_senegal_number(raw_number_text)
        if normalized_number is None:
            return
        with DatabaseTransaction(self.database_path) as connection:
            connection.execute("DELETE FROM blacklisted_numbers WHERE phone_number = ?", (normalized_number,))

    def is_blacklisted(self, raw_number_text):
        normalized_number = normalize_senegal_number(raw_number_text)
        if normalized_number is None:
            return False
        with DatabaseTransaction(self.database_path) as connection:
            return connection.execute(
                "SELECT 1 FROM blacklisted_numbers WHERE phone_number = ?", (normalized_number,)
            ).fetchone() is not None

    def list_numbers(self):
        """Every blacklisted number, as a set of '+221…' strings."""
        with DatabaseTransaction(self.database_path) as connection:
            return {row[0] for row in connection.execute("SELECT phone_number FROM blacklisted_numbers")}
