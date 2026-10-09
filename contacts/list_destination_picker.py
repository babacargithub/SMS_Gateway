"""« Liste de destination » block shared by every import: a new list (name asked) or an existing one."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QLineEdit, QRadioButton, QVBoxLayout, QWidget

from common.french_labels import format_contact_count

DEFAULT_TEXT_STYLE = "color: gray;"
ERROR_TEXT_STYLE = "color: #c0392b;"


class ListDestinationPicker(QWidget):
    changed = Signal()   # the choice or its validity changed

    def __init__(self, existing_lists, list_name_exists, default_list_name="", existing_list_hint="", parent=None):
        super().__init__(parent)
        self.list_name_exists = list_name_exists
        self.existing_list_hint = existing_list_hint

        self.new_list_radio = QRadioButton("Créer une nouvelle liste :")
        self.new_list_name_input = QLineEdit(default_list_name)
        self.new_list_name_input.setPlaceholderText("ex. Fidèles clients")
        self.existing_list_radio = QRadioButton("Ajouter à une liste existante :")
        self.existing_list_combo = QComboBox()
        for contact_list in existing_lists:
            self.existing_list_combo.addItem(f"{contact_list.name}  ({format_contact_count(contact_list.contact_count)})", contact_list.list_id)
        self.existing_list_radio.setEnabled(bool(existing_lists))
        self.existing_list_combo.setEnabled(False)
        self.new_list_radio.setChecked(True)
        self.list_name_problem_label = QLabel()
        self.list_name_problem_label.setWordWrap(True)

        destination_layout = QFormLayout()
        destination_layout.addRow(self.new_list_radio, self.new_list_name_input)
        destination_layout.addRow("", self.list_name_problem_label)
        destination_layout.addRow(self.existing_list_radio, self.existing_list_combo)
        picker_layout = QVBoxLayout(self)
        picker_layout.setContentsMargins(0, 0, 0, 0)
        picker_layout.addWidget(QLabel("<b>Liste de destination</b>"))
        picker_layout.addLayout(destination_layout)

        self.new_list_radio.toggled.connect(self.refresh_destination)
        self.new_list_name_input.textChanged.connect(self.refresh_destination)
        self.existing_list_combo.currentIndexChanged.connect(lambda *_: self.changed.emit())

    def chosen_new_list_name(self):
        """The new list's name, or None when adding to an existing list."""
        return self.new_list_name_input.text().strip() if self.new_list_radio.isChecked() else None

    def chosen_existing_list_id(self):
        return None if self.new_list_radio.isChecked() else self.existing_list_combo.currentData()

    def is_valid(self):
        if not self.new_list_radio.isChecked():
            return self.existing_list_combo.currentData() is not None
        new_list_name = self.new_list_name_input.text().strip()
        if not new_list_name:
            self.list_name_problem_label.setText("Donnez un nom à la nouvelle liste.")
        elif self.list_name_exists(new_list_name):
            self.list_name_problem_label.setText(f"Une liste nommée « {new_list_name} » existe déjà. Choisissez un autre nom ou ajoutez-y les contacts.")
        else:
            self.list_name_problem_label.setText("")
            return True
        self.list_name_problem_label.setStyleSheet(ERROR_TEXT_STYLE)
        return False

    def refresh_destination(self):
        is_new_list = self.new_list_radio.isChecked()
        self.new_list_name_input.setEnabled(is_new_list)
        self.existing_list_combo.setEnabled(not is_new_list)
        if is_new_list:
            self.is_valid()
        else:
            self.list_name_problem_label.setStyleSheet(DEFAULT_TEXT_STYLE)
            self.list_name_problem_label.setText(self.existing_list_hint)
        self.changed.emit()
