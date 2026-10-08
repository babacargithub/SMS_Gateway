"""« Configurer Orange SMS » : the four values come from the Orange developer account (same as GoTransV5's ORANGE_API_*)."""
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout

from gateways.gateway_credentials import OrangeSmsCredentials


class OrangeSettingsDialog(QDialog):
    def __init__(self, parent, credentials_store, current_credentials):
        super().__init__(parent)
        self.setWindowTitle("Configurer Orange SMS")
        self.setMinimumWidth(460)
        self.credentials_store = credentials_store
        self.saved = False

        current_credentials = current_credentials or OrangeSmsCredentials("", "", "+221200600", "")
        self.client_id_input = QLineEdit(current_credentials.client_id)
        self.client_secret_input = QLineEdit(current_credentials.client_secret)
        self.client_secret_input.setEchoMode(QLineEdit.Password)
        self.sender_number_input = QLineEdit(current_credentials.sender_number)
        self.sender_name_input = QLineEdit(current_credentials.sender_name)
        self.sender_name_input.setPlaceholderText("Facultatif, affiché à la place du numéro")

        form_layout = QFormLayout()
        form_layout.addRow("Identifiant client :", self.client_id_input)
        form_layout.addRow("Secret client :", self.client_secret_input)
        form_layout.addRow("Numéro expéditeur :", self.sender_number_input)
        form_layout.addRow("Nom d'expéditeur :", self.sender_name_input)

        keychain_note = QLabel("Les identifiants sont enregistrés dans le trousseau d'accès de macOS, jamais dans un fichier.")
        keychain_note.setWordWrap(True)
        keychain_note.setStyleSheet("color: #64748B;")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Enregistrer")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self.save_credentials)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form_layout)
        layout.addWidget(keychain_note)
        layout.addWidget(buttons)

    def save_credentials(self):
        credentials = OrangeSmsCredentials(
            self.client_id_input.text().strip(), self.client_secret_input.text().strip(),
            self.sender_number_input.text().strip(), self.sender_name_input.text().strip(),
        )
        if not credentials.is_complete():
            QMessageBox.warning(self, "Orange SMS", "L'identifiant, le secret et le numéro expéditeur sont obligatoires.")
            return
        try:
            self.credentials_store.save(credentials)
        except RuntimeError as keychain_error:
            QMessageBox.warning(self, "Orange SMS", str(keychain_error))
            return
        self.saved = True
        self.accept()
