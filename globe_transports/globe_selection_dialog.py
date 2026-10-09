"""Pick departs / buses on Globe Transports and fetch the contacts of their customers."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from common.french_labels import choose_singular_or_plural, format_number
from globe_transports.background_request import run_in_background
from globe_transports.globe_settings import load_base_url, save_base_url
from globe_transports.globe_token_store import KeychainTokenStore
from globe_transports.login_prompter import LoginPrompter
from globe_transports.globe_transports_client import (
    FILTER_ALL,
    FILTER_PAID,
    FILTER_UNPAID,
    PAST_PERIOD,
    UPCOMING_PERIOD,
    GlobeTransportsClient,
)

def default_client_factory(base_url, login_requester):
    return GlobeTransportsClient(base_url, token_store=KeychainTokenStore(), login_requester=login_requester)


GRAY_TEXT = "color: gray;"
RED_TEXT = "color: #c0392b;"
ID_ROLE = Qt.UserRole
PAYMENT_FILTER_CHOICES = [("Tous", FILTER_ALL), ("Payés", FILTER_PAID), ("Non payés", FILTER_UNPAID)]


class GlobeSelectionDialog(QDialog):
    """After `exec()` returns Accepted, `fetched_bookings` holds the bookings (dicts) of the checked departs and buses."""

    def __init__(self, parent=None, client_factory=default_client_factory, token_store=None):
        """`client_factory(base_url, login_requester)` builds the client; `token_store` is the one the logout button empties."""
        super().__init__(parent)
        self.setWindowTitle("Importer depuis Globe Transports")
        self.resize(640, 640)
        self.client_factory = client_factory
        self.token_store = token_store or KeychainTokenStore()
        self.login_prompter = LoginPrompter(self)
        self.fetched_bookings = []
        self.request_generation = 0     # answers of an older request (other period, closed dialog) are ignored
        self.is_busy = False

        self.base_url_input = QLineEdit(load_base_url())
        self.base_url_input.setPlaceholderText("http://127.0.0.1:8000")
        self.refresh_button = QPushButton("Actualiser")
        self.logout_button = QPushButton("Se déconnecter de Globe Transports")
        self.logout_button.setToolTip("Oublie la connexion enregistrée : il faudra saisir de nouveau le nom d'utilisateur et le mot de passe.")
        self.upcoming_radio = QRadioButton("À venir")
        self.past_radio = QRadioButton("Passés")
        self.upcoming_radio.setChecked(True)
        self.period_group = QButtonGroup(self)
        self.period_group.addButton(self.upcoming_radio)
        self.period_group.addButton(self.past_radio)

        self.departs_tree = QTreeWidget()
        self.departs_tree.setHeaderHidden(True)
        self.departs_tree.setRootIsDecorated(True)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.selection_label = QLabel("")
        self.selection_label.setStyleSheet(GRAY_TEXT)

        self.payment_filter_combo = QComboBox()
        for filter_label, filter_value in PAYMENT_FILTER_CHOICES:
            self.payment_filter_combo.addItem(filter_label, filter_value)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.import_button = self.button_box.button(QDialogButtonBox.Ok)
        self.import_button.setText("Importer")
        self.button_box.button(QDialogButtonBox.Cancel).setText("Annuler")
        self.button_box.accepted.connect(self.start_fetching_contacts)
        self.button_box.rejected.connect(self.reject)

        server_layout = QHBoxLayout()
        server_layout.addWidget(QLabel("Serveur :"))
        server_layout.addWidget(self.base_url_input, stretch=1)
        server_layout.addWidget(self.refresh_button)
        period_layout = QHBoxLayout()
        period_layout.addWidget(QLabel("Départs :"))
        period_layout.addWidget(self.upcoming_radio)
        period_layout.addWidget(self.past_radio)
        period_layout.addStretch()
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Réservations :"))
        filter_layout.addWidget(self.payment_filter_combo)
        filter_layout.addStretch()
        dialog_layout = QVBoxLayout(self)
        dialog_layout.addLayout(server_layout)
        logout_layout = QHBoxLayout()
        logout_layout.addStretch()
        logout_layout.addWidget(self.logout_button)
        dialog_layout.addLayout(logout_layout)
        dialog_layout.addLayout(period_layout)
        dialog_layout.addWidget(self.departs_tree, stretch=1)
        dialog_layout.addWidget(self.selection_label)
        dialog_layout.addLayout(filter_layout)
        dialog_layout.addWidget(self.status_label)
        dialog_layout.addWidget(self.button_box)

        self.upcoming_radio.toggled.connect(self.load_departs)
        self.refresh_button.clicked.connect(self.load_departs)
        self.logout_button.clicked.connect(self.log_out)
        self.base_url_input.returnPressed.connect(self.load_departs)
        self.departs_tree.itemChanged.connect(self.refresh_controls)
        self.refresh_controls()

    def showEvent(self, show_event):
        super().showEvent(show_event)
        if not self.departs_tree.topLevelItemCount() and not self.is_busy:
            self.load_departs()

    def done(self, result_code):
        self.request_generation += 1   # whatever is still in flight is no longer wanted
        super().done(result_code)

    # ---- the tree -----------------------------------------------------------------------------

    def current_period(self):
        return UPCOMING_PERIOD if self.upcoming_radio.isChecked() else PAST_PERIOD

    def populate_tree(self, departs):
        self.departs_tree.blockSignals(True)
        self.departs_tree.clear()
        for depart in departs:
            depart_item = QTreeWidgetItem([depart.name])
            depart_item.setData(0, ID_ROLE, depart.depart_id)
            depart_item.setFlags(depart_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            depart_item.setCheckState(0, Qt.Unchecked)
            for bus in depart.buses:
                bus_item = QTreeWidgetItem([bus.name])
                bus_item.setData(0, ID_ROLE, bus.bus_id)
                bus_item.setFlags(bus_item.flags() | Qt.ItemIsUserCheckable)
                bus_item.setCheckState(0, Qt.Unchecked)
                depart_item.addChild(bus_item)
            self.departs_tree.addTopLevelItem(depart_item)
            depart_item.setExpanded(True)
        self.departs_tree.blockSignals(False)
        self.refresh_controls()

    def selected_ids(self):
        """(depart ids, bus ids): a fully checked depart is asked as a whole, otherwise only its checked buses."""
        depart_ids, bus_ids = [], []
        for depart_index in range(self.departs_tree.topLevelItemCount()):
            depart_item = self.departs_tree.topLevelItem(depart_index)
            depart_state = depart_item.checkState(0)
            if depart_state == Qt.Checked:
                depart_ids.append(depart_item.data(0, ID_ROLE))
            elif depart_state == Qt.PartiallyChecked:
                bus_ids += [
                    depart_item.child(bus_index).data(0, ID_ROLE)
                    for bus_index in range(depart_item.childCount())
                    if depart_item.child(bus_index).checkState(0) == Qt.Checked
                ]
        return depart_ids, bus_ids

    def refresh_controls(self, *_):
        depart_ids, bus_ids = self.selected_ids()
        selected_count = len(depart_ids) + len(bus_ids)
        self.selection_label.setText(self.describe_selection(depart_ids, bus_ids))
        self.import_button.setEnabled(selected_count > 0 and not self.is_busy)
        for widget in (self.refresh_button, self.logout_button, self.upcoming_radio, self.past_radio, self.payment_filter_combo, self.base_url_input, self.departs_tree):
            widget.setEnabled(not self.is_busy)

    @staticmethod
    def describe_selection(depart_ids, bus_ids):
        parts = []
        if depart_ids:
            parts.append(f"{format_number(len(depart_ids))} {choose_singular_or_plural(len(depart_ids), 'départ', 'départs')}")
        if bus_ids:
            parts.append(f"{format_number(len(bus_ids))} {choose_singular_or_plural(len(bus_ids), 'bus', 'bus')}")
        return "Sélection : " + " + ".join(parts) if parts else "Rien de coché"

    def show_status(self, message, is_error=False):
        self.status_label.setStyleSheet(RED_TEXT if is_error else GRAY_TEXT)
        self.status_label.setText(message)

    def set_busy(self, is_busy):
        self.is_busy = is_busy
        self.refresh_controls()

    # ---- talking to the backend ---------------------------------------------------------------

    def build_client(self):
        save_base_url(self.base_url_input.text())
        client = self.client_factory(load_base_url(), self.login_prompter.request_login)
        self.login_prompter.login_function = client.login
        self.login_prompter.base_url = client.base_url
        return client

    def log_out(self):
        """Forget the stored token: the next request asks for the username and password again."""
        self.token_store.clear()
        self.request_generation += 1
        self.departs_tree.clear()
        self.refresh_controls()
        self.show_status("Vous êtes déconnecté de Globe Transports. Cliquez sur « Actualiser » pour vous reconnecter.")

    def load_departs(self, *_):
        if self.is_busy:
            return
        self.request_generation += 1
        this_generation = self.request_generation
        client, period = self.build_client(), self.current_period()
        self.base_url_input.setText(client.base_url)
        self.departs_tree.clear()
        self.show_status("Chargement des départs…")
        self.set_busy(True)
        run_in_background(
            lambda: client.fetch_departs(period),
            lambda departs: self.on_departs_loaded(this_generation, departs),
            lambda message: self.on_departs_failed(this_generation, message),
        )

    def on_departs_loaded(self, generation, departs):
        if generation != self.request_generation:
            return
        self.set_busy(False)
        self.populate_tree(departs)
        self.show_status("" if departs else "Aucun départ " + ("à venir." if self.upcoming_radio.isChecked() else "passé."))

    def on_departs_failed(self, generation, message):
        if generation != self.request_generation:
            return
        self.set_busy(False)
        self.show_status(message, is_error=True)

    def start_fetching_contacts(self):
        depart_ids, bus_ids = self.selected_ids()
        if not (depart_ids or bus_ids) or self.is_busy:
            return
        self.request_generation += 1
        this_generation = self.request_generation
        client, payment_filter = self.build_client(), self.payment_filter_combo.currentData()
        self.show_status("Récupération des contacts…")
        self.set_busy(True)
        run_in_background(
            lambda: client.fetch_bookings_for_selection(depart_ids, bus_ids, payment_filter),
            lambda bookings: self.on_contacts_fetched(this_generation, bookings),
            lambda message: self.on_contacts_failed(this_generation, message),
        )

    def on_contacts_fetched(self, generation, bookings):
        if generation != self.request_generation:
            return
        self.set_busy(False)
        if not bookings:
            self.show_status("Aucune réservation ne correspond à cette sélection et à ce filtre.", is_error=True)
            return
        self.fetched_bookings = bookings
        self.accept()

    def on_contacts_failed(self, generation, message):
        if generation != self.request_generation:
            return
        self.set_busy(False)
        self.show_status(message, is_error=True)
