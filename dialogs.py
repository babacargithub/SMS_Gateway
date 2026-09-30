from PySide6.QtWidgets import QMessageBox


def ask_yes_no_defaulting_to_no(parent, title, question):
    """Destructive actions: the safe answer (Non) is the default button."""
    return QMessageBox.question(parent, title, question, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
