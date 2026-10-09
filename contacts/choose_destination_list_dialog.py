"""Asks which list receives contacts that are already read (not from a file): a new list or an existing one."""
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from common.french_labels import format_contact_count
from contacts.list_destination_picker import ListDestinationPicker


class ChooseDestinationListDialog(QDialog):
    def __init__(self, title, summary_text, contact_count, existing_lists, list_name_exists, default_list_name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.summary_label = QLabel(summary_text)
        self.summary_label.setWordWrap(True)
        self.destination_picker = ListDestinationPicker(existing_lists, list_name_exists, default_list_name)
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.import_button = self.button_box.button(QDialogButtonBox.Ok)
        self.import_button.setText(f"Importer {format_contact_count(contact_count)}")
        self.button_box.button(QDialogButtonBox.Cancel).setText("Annuler")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        dialog_layout = QVBoxLayout(self)
        dialog_layout.addWidget(self.summary_label)
        dialog_layout.addWidget(self.destination_picker)
        dialog_layout.addWidget(self.button_box)
        self.destination_picker.changed.connect(self.refresh_import_button)
        self.refresh_import_button()
        self.destination_picker.new_list_name_input.setFocus()

    def refresh_import_button(self):
        self.import_button.setEnabled(self.destination_picker.is_valid())

    def chosen_new_list_name(self):
        return self.destination_picker.chosen_new_list_name()

    def chosen_existing_list_id(self):
        return self.destination_picker.chosen_existing_list_id()
