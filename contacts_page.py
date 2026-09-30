from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from contact_import import read_spreadsheet
from dialogs import ask_yes_no_defaulting_to_no
from french_labels import format_contact_count, format_custom_fields, format_french_date
from contact_store import ListNameAlreadyExists
from table_helpers import POSITION_COLUMN_TITLE, POSITION_COLUMN_WIDTH_PIXELS, build_position_item
from icons import EDIT_ICON_SHAPES, TRASH_ICON_SHAPES, build_colored_icon
from import_contacts_dialog import ImportContactsDialog

LIST_COLUMN_TITLES = [POSITION_COLUMN_TITLE, "Liste", "Contacts", "Créée le", "Actions"]
ACTIONS_COLUMN = 4
EDIT_ICON_COLOR = "#3B82F6"
DELETE_ICON_COLOR = "#EF4444"
ICON_BUTTON_SIZE_PIXELS = 32
ICON_BUTTON_STYLE = (
    "QToolButton { border: none; border-radius: 6px; }"
    "QToolButton:hover { background: rgba(128, 128, 128, 0.22); }"
)
CONTACT_COLUMN_TITLES = [POSITION_COLUMN_TITLE, "Nom", "Téléphone", "Détails"]
FILE_DIALOG_FILTER = "Fichiers de contacts (*.xlsx *.xlsm *.csv);;Tous les fichiers (*)"
EMPTY_STATE_STYLE = "font-size: 16px; color: gray;"


def build_icon_button(icon_shapes, icon_color, tooltip_text, on_click):
    icon_button = QToolButton()
    icon_button.setIcon(build_colored_icon(icon_shapes, icon_color))
    icon_button.setToolTip(tooltip_text)
    icon_button.setFixedSize(ICON_BUTTON_SIZE_PIXELS, ICON_BUTTON_SIZE_PIXELS)
    icon_button.setStyleSheet(ICON_BUTTON_STYLE)
    icon_button.setCursor(Qt.PointingHandCursor)
    icon_button.clicked.connect(on_click)
    return icon_button


class ContactsPage(QWidget):
    """Two views: the lists (with their contact counts), and the contacts of one opened list."""

    def __init__(self, contact_store):
        super().__init__()
        self.contact_store = contact_store
        self.contact_lists = []
        self.opened_list = None
        self.displayed_contact_ids = []

        self.stacked_views = QStackedWidget()
        self.lists_view = self.build_lists_view()
        self.contacts_view = self.build_contacts_view()
        self.stacked_views.addWidget(self.lists_view)
        self.stacked_views.addWidget(self.contacts_view)
        page_layout = QVBoxLayout(self)
        page_layout.addWidget(self.stacked_views)

        self.show_lists()

    # ---- lists view --------------------------------------------------------------------------

    def build_lists_view(self):
        self.import_button = QPushButton("Importer depuis Excel / CSV...")
        self.import_button.clicked.connect(self.start_import)
        toolbar_layout = QHBoxLayout()
        toolbar_layout.addWidget(self.import_button)
        toolbar_layout.addStretch()

        self.lists_table = QTableWidget(0, len(LIST_COLUMN_TITLES))
        self.lists_table.setHorizontalHeaderLabels(LIST_COLUMN_TITLES)
        self.lists_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.lists_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.lists_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.lists_table.verticalHeader().setVisible(False)
        self.lists_table.verticalHeader().setDefaultSectionSize(44)
        self.lists_table.viewport().setCursor(Qt.PointingHandCursor)
        header = self.lists_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(ACTIONS_COLUMN, QHeaderView.ResizeToContents)
        self.lists_table.setColumnWidth(0, POSITION_COLUMN_WIDTH_PIXELS)
        self.lists_table.cellClicked.connect(self.open_list_from_row)

        self.no_lists_label = QLabel("Aucune liste de contacts pour le moment.\nImportez un fichier Excel ou CSV pour créer votre première liste.")
        self.no_lists_label.setAlignment(Qt.AlignCenter)
        self.no_lists_label.setStyleSheet(EMPTY_STATE_STYLE)

        view = QWidget()
        view_layout = QVBoxLayout(view)
        view_layout.addLayout(toolbar_layout)
        view_layout.addWidget(self.lists_table)
        view_layout.addWidget(self.no_lists_label)
        return view

    def show_lists(self):
        self.opened_list = None
        self.contact_lists = self.contact_store.list_contact_lists()
        self.lists_table.setRowCount(len(self.contact_lists))
        for row_index, contact_list in enumerate(self.contact_lists):
            name_item = QTableWidgetItem(contact_list.name)
            name_font = name_item.font()
            name_font.setBold(True)
            name_item.setFont(name_font)
            self.lists_table.setItem(row_index, 0, build_position_item(row_index + 1))
            self.lists_table.setItem(row_index, 1, name_item)
            self.lists_table.setItem(row_index, 2, QTableWidgetItem(format_contact_count(contact_list.contact_count)))
            self.lists_table.setItem(row_index, 3, QTableWidgetItem(format_french_date(contact_list.created_date)))
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(6, 0, 6, 0)
            actions_layout.setSpacing(4)
            actions_layout.addWidget(
                build_icon_button(EDIT_ICON_SHAPES, EDIT_ICON_COLOR, "Renommer la liste", partial(self.rename_list, contact_list))
            )
            actions_layout.addWidget(
                build_icon_button(TRASH_ICON_SHAPES, DELETE_ICON_COLOR, "Supprimer la liste", partial(self.delete_list, contact_list))
            )
            self.lists_table.setCellWidget(row_index, ACTIONS_COLUMN, actions_widget)
        self.no_lists_label.setVisible(not self.contact_lists)
        self.lists_table.setVisible(bool(self.contact_lists))
        self.stacked_views.setCurrentWidget(self.lists_view)

    def open_list_from_row(self, row_index, column_index):
        if column_index == ACTIONS_COLUMN:
            return  # the icon buttons handle their own clicks
        self.open_list(self.contact_lists[row_index])

    def rename_list(self, contact_list):
        new_name, was_confirmed = QInputDialog.getText(self, "Renommer la liste", "Nouveau nom :", text=contact_list.name)
        new_name = new_name.strip()
        if not was_confirmed or not new_name or new_name == contact_list.name:
            return
        try:
            self.contact_store.rename_list(contact_list.list_id, new_name)
        except ListNameAlreadyExists:
            QMessageBox.warning(self, "La liste existe déjà", f"Une liste nommée « {new_name} » existe déjà.")
            return
        self.show_lists()

    def delete_list(self, contact_list):
        confirmed = ask_yes_no_defaulting_to_no(
            self,
            "Supprimer la liste",
            f"Supprimer la liste « {contact_list.name} » et ses {format_contact_count(contact_list.contact_count)} ?\n\n"
            "Cette action est irréversible.",
        )
        if confirmed:
            self.contact_store.delete_list(contact_list.list_id)
            self.show_lists()

    # ---- contacts of one list ----------------------------------------------------------------

    def build_contacts_view(self):
        self.back_button = QPushButton("←  Toutes les listes")
        self.back_button.clicked.connect(self.show_lists)
        self.list_title_label = QLabel("")
        self.list_title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        title_layout = QHBoxLayout()
        title_layout.addWidget(self.back_button)
        title_layout.addWidget(self.list_title_label)
        title_layout.addStretch()

        self.delete_contacts_button = QPushButton("Supprimer la sélection")
        self.delete_contacts_button.clicked.connect(self.delete_selected_contacts)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Rechercher un nom ou un numéro...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.apply_search_filter)
        self.count_label = QLabel("")
        toolbar_layout = QHBoxLayout()
        toolbar_layout.addWidget(self.delete_contacts_button)
        toolbar_layout.addWidget(self.search_input, stretch=1)
        toolbar_layout.addWidget(self.count_label)

        self.contacts_table = QTableWidget(0, len(CONTACT_COLUMN_TITLES))
        self.contacts_table.setHorizontalHeaderLabels(CONTACT_COLUMN_TITLES)
        self.contacts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.contacts_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.contacts_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.contacts_table.verticalHeader().setVisible(False)
        self.contacts_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.contacts_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.contacts_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.contacts_table.horizontalHeader().setStretchLastSection(True)
        self.contacts_table.setColumnWidth(0, POSITION_COLUMN_WIDTH_PIXELS)
        self.contacts_table.setColumnWidth(1, 220)

        self.no_contacts_label = QLabel("Cette liste ne contient aucun contact.")
        self.no_contacts_label.setAlignment(Qt.AlignCenter)
        self.no_contacts_label.setStyleSheet(EMPTY_STATE_STYLE)

        view = QWidget()
        view_layout = QVBoxLayout(view)
        view_layout.addLayout(title_layout)
        view_layout.addLayout(toolbar_layout)
        view_layout.addWidget(self.contacts_table)
        view_layout.addWidget(self.no_contacts_label)
        return view

    def open_list(self, contact_list):
        self.opened_list = contact_list
        self.list_title_label.setText(contact_list.name)
        self.search_input.clear()
        self.reload_contacts()
        self.stacked_views.setCurrentWidget(self.contacts_view)

    def reload_contacts(self):
        stored_contacts = self.contact_store.list_contacts(self.opened_list.list_id)
        self.displayed_contact_ids = [contact.contact_id for contact in stored_contacts]
        self.contacts_table.setUpdatesEnabled(False)
        self.contacts_table.setRowCount(len(stored_contacts))
        for row_index, contact in enumerate(stored_contacts):
            self.contacts_table.setItem(row_index, 0, build_position_item(row_index + 1))  # stays fixed when searching
            self.contacts_table.setItem(row_index, 1, QTableWidgetItem(contact.name))
            self.contacts_table.setItem(row_index, 2, QTableWidgetItem(contact.phone_number))
            self.contacts_table.setItem(row_index, 3, QTableWidgetItem(format_custom_fields(contact.custom_fields)))
        self.contacts_table.setUpdatesEnabled(True)
        self.no_contacts_label.setVisible(not stored_contacts)
        self.contacts_table.setVisible(bool(stored_contacts))
        self.apply_search_filter()

    def apply_search_filter(self):
        search_text = self.search_input.text().strip().lower()
        visible_count = 0
        for row_index in range(self.contacts_table.rowCount()):
            row_text = (
                self.contacts_table.item(row_index, 1).text() + " " + self.contacts_table.item(row_index, 2).text()
            ).lower()
            is_match = search_text in row_text
            self.contacts_table.setRowHidden(row_index, not is_match)
            visible_count += is_match
        total_count = self.contacts_table.rowCount()
        self.count_label.setText(
            format_contact_count(total_count) if visible_count == total_count else f"{visible_count} sur {format_contact_count(total_count)}"
        )

    def delete_selected_contacts(self):
        selected_row_indexes = sorted({index.row() for index in self.contacts_table.selectedIndexes()})
        if not selected_row_indexes:
            return
        confirmed = ask_yes_no_defaulting_to_no(
            self,
            "Supprimer des contacts",
            f"Supprimer {len(selected_row_indexes)} contact(s) sélectionné(s) de la liste « {self.opened_list.name} » ?\n\n"
            "Cette action est irréversible.",
        )
        if confirmed:
            self.contact_store.delete_contacts([self.displayed_contact_ids[row_index] for row_index in selected_row_indexes])
            self.reload_contacts()

    # ---- importing ---------------------------------------------------------------------------

    def start_import(self):
        chosen_file_path, _ = QFileDialog.getOpenFileName(self, "Choisir un fichier de contacts", "", FILE_DIALOG_FILTER)
        if not chosen_file_path:
            return
        file_name = Path(chosen_file_path).name
        try:
            headers, data_rows = read_spreadsheet(chosen_file_path)
        except Exception as read_error:  # bad file, wrong format, permissions: tell the user, don't crash
            QMessageBox.critical(self, "Lecture impossible", str(read_error))
            return
        if not data_rows:
            QMessageBox.warning(self, "Rien à importer", "Le fichier contient une ligne d'en-tête mais aucun contact.")
            return

        import_dialog = ImportContactsDialog(
            file_name,
            headers,
            data_rows,
            existing_lists=self.contact_store.list_contact_lists(),
            list_name_exists=self.contact_store.list_name_exists,
            default_list_name=Path(chosen_file_path).stem,
            parent=self,
        )
        if import_dialog.exec() != ImportContactsDialog.Accepted:
            return

        valid_contacts = import_dialog.import_plan.valid_contacts
        try:
            if import_dialog.chosen_new_list_name():
                target_list_id, inserted_count, skipped_count = self.contact_store.import_into_new_list(
                    import_dialog.chosen_new_list_name(), valid_contacts, file_name
                )
            else:
                target_list_id = import_dialog.chosen_existing_list_id()
                inserted_count, skipped_count = self.contact_store.import_into_existing_list(
                    target_list_id, valid_contacts, file_name
                )
        except ListNameAlreadyExists:
            QMessageBox.warning(self, "La liste existe déjà", "Une liste portant ce nom vient d'être créée. Rien n'a été importé.")
            return

        target_list = next(
            contact_list for contact_list in self.contact_store.list_contact_lists() if contact_list.list_id == target_list_id
        )
        QMessageBox.information(
            self,
            "Import terminé",
            f"{inserted_count} contact(s) ajouté(s) à la liste « {target_list.name} ».\n"
            f"{skipped_count} ignoré(s) car le numéro est déjà dans cette liste.",
        )
        self.open_list(target_list)
