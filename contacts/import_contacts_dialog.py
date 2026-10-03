from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from common.french_labels import format_contact_count
from contacts.contact_import import build_import_plan, guess_name_column, guess_phone_column

PREVIEW_ROW_COUNT = 5
INVALID_ROWS_SHOWN = 15
NO_NAME_COLUMN_LABEL = "(aucune)"
DEFAULT_TEXT_STYLE = "color: gray;"
ERROR_TEXT_STYLE = "color: #c0392b;"


class ImportContactsDialog(QDialog):
    """Lets the user confirm which column is the phone number / name, and shows what will happen."""

    def __init__(self, file_name, headers, data_rows, existing_lists, list_name_exists, default_list_name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Importer des contacts : {file_name}")
        self.resize(720, 700)
        self.existing_lists = existing_lists
        self.list_name_exists = list_name_exists
        self.headers = headers
        self.data_rows = data_rows
        self.import_plan = None

        self.phone_column_combo = QComboBox()
        self.phone_column_combo.addItems(headers)
        self.name_column_combo = QComboBox()
        self.name_column_combo.addItem(NO_NAME_COLUMN_LABEL)
        self.name_column_combo.addItems(headers)

        guessed_phone_column = guess_phone_column(headers, data_rows)
        if guessed_phone_column is not None:
            self.phone_column_combo.setCurrentIndex(guessed_phone_column)
        guessed_name_column = guess_name_column(headers, self.phone_column_combo.currentIndex())
        if guessed_name_column is not None:
            self.name_column_combo.setCurrentIndex(guessed_name_column + 1)

        self.preview_table = QTableWidget(min(PREVIEW_ROW_COUNT, len(data_rows)), len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        self.preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row_index, row in enumerate(data_rows[:PREVIEW_ROW_COUNT]):
            for column_index, cell_text in enumerate(row):
                self.preview_table.setItem(row_index, column_index, QTableWidgetItem(cell_text))

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

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.problem_rows_text = QPlainTextEdit()
        self.problem_rows_text.setReadOnly(True)
        self.problem_rows_text.setMaximumHeight(120)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.button(QDialogButtonBox.Cancel).setText("Annuler")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        form_layout = QFormLayout()
        form_layout.addRow("Colonne du numéro de téléphone :", self.phone_column_combo)
        form_layout.addRow("Colonne du nom :", self.name_column_combo)
        destination_layout = QFormLayout()
        destination_layout.addRow(self.new_list_radio, self.new_list_name_input)
        destination_layout.addRow("", self.list_name_problem_label)
        destination_layout.addRow(self.existing_list_radio, self.existing_list_combo)
        dialog_layout = QVBoxLayout(self)
        dialog_layout.addLayout(form_layout)
        dialog_layout.addWidget(QLabel(f"Aperçu ({self.preview_table.rowCount()} premières lignes) :"))
        dialog_layout.addWidget(self.preview_table)
        dialog_layout.addWidget(QLabel("<b>Liste de destination</b>"))
        dialog_layout.addLayout(destination_layout)
        dialog_layout.addWidget(self.summary_label)
        dialog_layout.addWidget(self.problem_rows_text)
        dialog_layout.addWidget(self.button_box)

        self.phone_column_combo.currentIndexChanged.connect(self.refresh_import_plan)
        self.name_column_combo.currentIndexChanged.connect(self.refresh_import_plan)
        self.new_list_radio.toggled.connect(self.refresh_destination)
        self.new_list_name_input.textChanged.connect(self.refresh_destination)
        self.refresh_import_plan()
        self.new_list_name_input.setFocus()

    def chosen_new_list_name(self):
        """The new list's name, or None when adding to an existing list."""
        return self.new_list_name_input.text().strip() if self.new_list_radio.isChecked() else None

    def chosen_existing_list_id(self):
        return None if self.new_list_radio.isChecked() else self.existing_list_combo.currentData()

    def destination_is_valid(self):
        if not self.new_list_radio.isChecked():
            return True
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
            self.destination_is_valid()
        else:
            self.list_name_problem_label.setText("")
        self.refresh_import_button()

    def refresh_import_button(self):
        valid_contact_count = len(self.import_plan.valid_contacts) if self.import_plan else 0
        import_button = self.button_box.button(QDialogButtonBox.Ok)
        import_button.setText(f"Importer {format_contact_count(valid_contact_count)}")
        import_button.setEnabled(valid_contact_count > 0 and self.destination_is_valid())

    def refresh_import_plan(self):
        name_column_index = self.name_column_combo.currentIndex() - 1
        self.import_plan = build_import_plan(
            self.headers,
            self.data_rows,
            self.phone_column_combo.currentIndex(),
            name_column_index if name_column_index >= 0 else None,
        )
        plan = self.import_plan
        self.summary_label.setText(
            f"<b>{len(self.data_rows)}</b> lignes : <b>{len(plan.valid_contacts)}</b> valides, "
            f"{len(plan.invalid_rows)} numéro(s) invalide(s), {plan.empty_phone_count} sans numéro, "
            f"{plan.duplicate_count_in_file} doublon(s) dans le fichier. "
            "Les autres colonnes sont conservées dans « Détails »."
        )
        shown_invalid_rows = plan.invalid_rows[:INVALID_ROWS_SHOWN]
        problem_lines = [f"Ligne {row_number} : « {raw_text} » n'est pas un numéro valide" for row_number, raw_text in shown_invalid_rows]
        if len(plan.invalid_rows) > INVALID_ROWS_SHOWN:
            problem_lines.append(f"... et {len(plan.invalid_rows) - INVALID_ROWS_SHOWN} autre(s)")
        self.problem_rows_text.setPlainText("\n".join(problem_lines) or "Aucun numéro invalide.")

        self.refresh_import_button()
