from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from common.french_labels import format_contact_count
from common.message_template import EXTRA_COLUMN_NAMES
from contacts.list_destination_picker import ListDestinationPicker
from contacts.contact_import import build_import_plan, guess_extra_column_indexes, guess_name_column, guess_phone_column

PREVIEW_ROW_COUNT = 5
INVALID_ROWS_SHOWN = 15
NO_NAME_COLUMN_LABEL = "(aucune)"
EXTRA_COLUMN_ROWS = 5   # the ten {colN} choices are laid out in two blocks of five


class ImportContactsDialog(QDialog):
    """Lets the user confirm which column is the phone number / name, and shows what will happen."""

    def __init__(self, file_name, headers, data_rows, existing_lists, list_name_exists, default_list_name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Importer des contacts : {file_name}")
        self.resize(860, 820)
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

        self.extra_column_combos = []
        extra_columns_box = QGroupBox("Colonnes supplémentaires : variables {col1} à {col10} du message")
        extra_columns_layout = QGridLayout(extra_columns_box)
        guessed_extra_columns = guess_extra_column_indexes(headers, self.phone_column_combo.currentIndex(), guessed_name_column)
        for extra_position, column_name in enumerate(EXTRA_COLUMN_NAMES):
            extra_column_combo = QComboBox()
            extra_column_combo.addItem(NO_NAME_COLUMN_LABEL)
            extra_column_combo.addItems(headers)
            guessed_column_index = guessed_extra_columns[extra_position]
            extra_column_combo.setCurrentIndex(0 if guessed_column_index is None else guessed_column_index + 1)
            extra_column_combo.currentIndexChanged.connect(self.refresh_import_plan)
            self.extra_column_combos.append(extra_column_combo)
            grid_row, grid_block = extra_position % EXTRA_COLUMN_ROWS, extra_position // EXTRA_COLUMN_ROWS
            extra_columns_layout.addWidget(QLabel("{" + column_name + "} :"), grid_row, grid_block * 2)
            extra_columns_layout.addWidget(extra_column_combo, grid_row, grid_block * 2 + 1)
        extra_columns_layout.setColumnStretch(1, 1)
        extra_columns_layout.setColumnStretch(3, 1)

        self.preview_table = QTableWidget(min(PREVIEW_ROW_COUNT, len(data_rows)), len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        self.preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row_index, row in enumerate(data_rows[:PREVIEW_ROW_COUNT]):
            for column_index, cell_text in enumerate(row):
                self.preview_table.setItem(row_index, column_index, QTableWidgetItem(cell_text))

        self.destination_picker = ListDestinationPicker(
            existing_lists, list_name_exists, default_list_name,
            existing_list_hint="Gardez les mêmes colonnes {col1} à {col10} que lors des imports précédents de cette liste.",
        )

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
        dialog_layout = QVBoxLayout(self)
        dialog_layout.addLayout(form_layout)
        dialog_layout.addWidget(extra_columns_box)
        dialog_layout.addWidget(QLabel(f"Aperçu ({self.preview_table.rowCount()} premières lignes) :"))
        dialog_layout.addWidget(self.preview_table)
        dialog_layout.addWidget(self.destination_picker)
        dialog_layout.addWidget(self.summary_label)
        dialog_layout.addWidget(self.problem_rows_text)
        dialog_layout.addWidget(self.button_box)

        self.phone_column_combo.currentIndexChanged.connect(self.refresh_import_plan)
        self.name_column_combo.currentIndexChanged.connect(self.refresh_import_plan)
        self.destination_picker.changed.connect(self.refresh_import_button)
        self.refresh_import_plan()
        self.destination_picker.new_list_name_input.setFocus()

    def chosen_new_list_name(self):
        return self.destination_picker.chosen_new_list_name()

    def chosen_existing_list_id(self):
        return self.destination_picker.chosen_existing_list_id()

    def refresh_import_button(self):
        valid_contact_count = len(self.import_plan.valid_contacts) if self.import_plan else 0
        import_button = self.button_box.button(QDialogButtonBox.Ok)
        import_button.setText(f"Importer {format_contact_count(valid_contact_count)}")
        import_button.setEnabled(valid_contact_count > 0 and self.destination_picker.is_valid())

    def refresh_import_plan(self):
        name_column_index = self.name_column_combo.currentIndex() - 1
        extra_column_indexes = [
            extra_column_combo.currentIndex() - 1 if extra_column_combo.currentIndex() > 0 else None
            for extra_column_combo in self.extra_column_combos
        ]
        self.import_plan = build_import_plan(
            self.headers,
            self.data_rows,
            self.phone_column_combo.currentIndex(),
            name_column_index if name_column_index >= 0 else None,
            extra_column_indexes,
        )
        plan = self.import_plan
        self.summary_label.setText(
            f"<b>{len(self.data_rows)}</b> lignes : <b>{len(plan.valid_contacts)}</b> valides, "
            f"{len(plan.invalid_rows)} numéro(s) invalide(s), {plan.empty_phone_count} sans numéro, "
            f"{plan.duplicate_count_in_file} doublon(s) dans le fichier. "
            "Toutes les autres colonnes sont conservées dans « Détails »."
        )
        shown_invalid_rows = plan.invalid_rows[:INVALID_ROWS_SHOWN]
        problem_lines = [f"Ligne {row_number} : « {raw_text} » n'est pas un numéro valide" for row_number, raw_text in shown_invalid_rows]
        if len(plan.invalid_rows) > INVALID_ROWS_SHOWN:
            problem_lines.append(f"... et {len(plan.invalid_rows) - INVALID_ROWS_SHOWN} autre(s)")
        self.problem_rows_text.setPlainText("\n".join(problem_lines) or "Aucun numéro invalide.")

        self.refresh_import_button()
