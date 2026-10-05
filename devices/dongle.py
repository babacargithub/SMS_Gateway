"""Device layer: raw-USB access to Huawei dongles. No UI code in here."""
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from common.sms_encoding import MAX_PARTS_PER_MESSAGE, build_submit_pdus, is_plain_single_sms, plan_sms
from inbox.received_sms_parser import parse_pdu_list_reply
from sim.ussd_codec import decode_ussd_reply, encode_ussd_request

import usb.backend.libusb1
import usb.core
import usb.util

HUAWEI_VENDOR_ID = 0x12D1
CDROM_PRODUCT_ID = 0x14FE
MODEM_PRODUCT_ID = 0x151D
LIBUSB_LIBRARY_PATH = "/opt/homebrew/lib/libusb-1.0.dylib"
VENDOR_SPECIFIC_INTERFACE_CLASS = 0xFF

MODE_SWITCH_HELPER_PYTHON = "/usr/local/libexec/sms-gateway/venv/bin/python"
MODE_SWITCH_HELPER_SCRIPT = "/usr/local/libexec/sms-gateway/dongle_mode_switcher.py"
MODE_SWITCH_HELPER_TIMEOUT_SECONDS = 60
SUDO_MISSING_RULE_EXIT_CODE = 1  # `sudo -n` exits with 1 when it would have asked for a password

SEND_TIMEOUT_SECONDS = 30
PAUSE_BETWEEN_PARTS_SECONDS = 1.0  # between the parts of one long message (each part is an SMS for the operator)
LIST_MESSAGES_TIMEOUT_SECONDS = 15
USSD_REPLY_TIMEOUT_SECONDS = 25  # USSD sessions time out after ~20-30 s

# `+CUSD: <status>,"<hex>",<dcs>` — the line is complete only once it ends with a line break.
USSD_REPLY_LINE_PATTERN = re.compile(
    r'\+CUSD:\s*(?P<status>\d+)(?:,"(?P<reply_hex>[0-9A-Fa-f]*)"(?:,(?P<data_coding_scheme>\d+))?)?[ \t]*\r?\n'
)
# The dongle announces a new SMS by itself with `+CMTI: "SM",<slot>` once AT+CNMI is set. Mode 2 buffers announcements while
# the port is busy; mode 1 is the fallback for firmwares that refuse it. Only the announcement is asked for (mt=1): the message
# stays on the SIM until the inbox has read and stored it, so a missed announcement never loses a message.
NEW_MESSAGE_NOTIFICATION_SETTINGS = ("2,1,0,0,0", "1,1,0,0,0")
NEW_MESSAGE_NOTICE_PATTERN = re.compile(r'\+CMTI:\s*"')
LISTEN_READ_TIMEOUT_MILLISECONDS = 200
# This dongle has a second AT port (interface 3) where it sends its unsolicited lines (+CMTI, ^RSSI...); the port that answers
# commands (interface 0) never receives the +CMTI. Verified by hand: seven announcements were waiting on interface 3.
NOTIFICATION_PORT_PROBE_SECONDS = 1.0

USSD_STATUS_AWAITING_REPLY = 1
USSD_STATUS_NOT_SUPPORTED = 4
USSD_STATUS_NETWORK_TIMEOUT = 5

STATE_DETECTED = "Détecté"
STATE_SWITCHING = "Changement de mode"
STATE_INITIALIZING = "Initialisation"
STATE_READY = "Prêt"
STATE_ERROR = "Erreur"


class SendRejected(Exception):
    """The dongle or network refused the message: it was definitely not sent."""


class SendOutcomeUnknown(Exception):
    """No confirmation either way: it may have been sent, so never retry automatically."""


def is_final_reply(reply_text):
    return reply_text.rstrip().endswith("OK") or "ERROR" in reply_text


def is_end_of_message_list(reply_text):
    """A message body may itself end with 'OK', so only an 'OK' on its own line ends the list."""
    stripped_reply = reply_text.rstrip()
    return stripped_reply.endswith("\nOK") or stripped_reply == "OK" or "+CMS ERROR" in stripped_reply or stripped_reply.endswith("\nERROR")


@dataclass
class UssdReply:
    status: int   # 0 final, 1 menu awaiting an answer, 2 ended by the network
    text: str

    @property
    def is_session_open(self):
        return self.status == USSD_STATUS_AWAITING_REPLY


@dataclass
class DongleStatus:
    usb_location: str
    state: str
    imei: Optional[str] = None
    operator_name: Optional[str] = None
    operator_network_code: Optional[str] = None  # MCC+MNC, e.g. "60803"
    signal_dbm: Optional[int] = None
    signal_bars: int = 0
    sim_status: Optional[str] = None
    network_registration: Optional[str] = None
    error_message: Optional[str] = None


def load_usb_backend():
    return usb.backend.libusb1.get_backend(
        find_library=lambda _: LIBUSB_LIBRARY_PATH
    ) or usb.backend.libusb1.get_backend()


def usb_location_of(usb_device):
    return f"{usb_device.bus}:{usb_device.address}"


def find_huawei_devices(usb_backend, product_id):
    return list(
        usb.core.find(
            find_all=True,
            idVendor=HUAWEI_VENDOR_ID,
            idProduct=product_id,
            backend=usb_backend,
        )
    )


def is_mode_switch_helper_installed():
    return os.path.exists(MODE_SWITCH_HELPER_PYTHON) and os.path.exists(MODE_SWITCH_HELPER_SCRIPT)


def switch_dongles_to_modem_mode_through_helper():
    """Switching needs root, and macOS only lets a process that belongs to a login session (like this app, started
    from a terminal) open the dongle's storage interface. So the app calls the root-owned helper through ONE passwordless
    sudo rule installed by mode_switcher/install_switcher.sh. Raises RuntimeError with a French message on failure."""
    switch_command = ["/usr/bin/sudo", "-n", MODE_SWITCH_HELPER_PYTHON, MODE_SWITCH_HELPER_SCRIPT, "--switch-now"]
    try:
        completed_process = subprocess.run(
            switch_command, capture_output=True, text=True, timeout=MODE_SWITCH_HELPER_TIMEOUT_SECONDS
        )
    except subprocess.SubprocessError as helper_error:
        raise RuntimeError(f"L'assistant de changement de mode ne répond pas : {helper_error}")
    if completed_process.returncode != 0:
        helper_output = (completed_process.stderr or "").strip().splitlines()
        last_helper_line = helper_output[-1] if helper_output else ""
        raise RuntimeError(f"Échec du changement de mode : {last_helper_line}")


def signal_value_to_dbm(csq_value):
    return None if csq_value == 99 else -113 + 2 * csq_value


def dbm_to_bars(signal_dbm):
    if signal_dbm is None:
        return 0
    for minimum_dbm, bars in ((-75, 4), (-85, 3), (-95, 2), (-105, 1)):
        if signal_dbm >= minimum_dbm:
            return bars
    return 0


class DongleConnection:
    """An open AT port on one modem-mode dongle. All I/O has a timeout."""

    def __init__(self, usb_device):
        self.usb_device = usb_device
        self.usb_location = usb_location_of(usb_device)
        self.bulk_out_endpoint = None
        self.bulk_in_endpoint = None
        self.claimed_interface_number = None
        self.notification_in_endpoint = None  # read side of the second AT port, if the dongle has one that answers
        self.notification_interface_number = None
        self.notifications_enabled = False    # True once the dongle accepted AT+CNMI: then the inbox is read on announcement
        self.new_message_notice_seen = False  # a +CMTI line was seen since the last `take_new_message_notice()`

    def open(self):
        try:
            active_configuration = self.usb_device.get_active_configuration()
        except usb.core.USBError:
            self.usb_device.set_configuration()
            active_configuration = self.usb_device.get_active_configuration()

        for interface in active_configuration:
            if interface.bInterfaceClass != VENDOR_SPECIFIC_INTERFACE_CLASS:
                continue
            out_endpoint = self._find_bulk_endpoint(interface, usb.util.ENDPOINT_OUT)
            in_endpoint = self._find_bulk_endpoint(interface, usb.util.ENDPOINT_IN)
            if out_endpoint is None or in_endpoint is None:
                continue
            try:
                usb.util.claim_interface(self.usb_device, interface.bInterfaceNumber)
            except usb.core.USBError:
                continue
            self.bulk_out_endpoint, self.bulk_in_endpoint = out_endpoint, in_endpoint
            self.claimed_interface_number = interface.bInterfaceNumber
            if "OK" in self.send_at_command("AT", wait_seconds=1.5):
                self.send_at_command("ATE0")
                self.send_at_command("AT^CURC=0")
                self.enable_new_message_notifications()
                self._open_notification_port(active_configuration)
                return
            usb.util.release_interface(self.usb_device, interface.bInterfaceNumber)
        raise RuntimeError("Aucune interface n'a répondu à 'AT'")

    def _open_notification_port(self, active_configuration):
        """Claim another interface that answers `AT`, only to read its unsolicited lines. Never fatal: without it the
        monitor still reads the SIM on its safety timer."""
        for interface in active_configuration:
            if interface.bInterfaceNumber == self.claimed_interface_number:
                continue
            if interface.bInterfaceClass != VENDOR_SPECIFIC_INTERFACE_CLASS:
                continue
            out_endpoint = self._find_bulk_endpoint(interface, usb.util.ENDPOINT_OUT)
            in_endpoint = self._find_bulk_endpoint(interface, usb.util.ENDPOINT_IN)
            if out_endpoint is None or in_endpoint is None:
                continue
            try:
                usb.util.claim_interface(self.usb_device, interface.bInterfaceNumber)
            except usb.core.USBError:
                continue
            try:
                out_endpoint.write(b"AT\r", timeout=1000)
                probe_reply, deadline = "", time.time() + NOTIFICATION_PORT_PROBE_SECONDS
                while time.time() < deadline and "OK" not in probe_reply:
                    try:
                        probe_reply += bytes(in_endpoint.read(512, timeout=200)).decode(errors="ignore")
                    except usb.core.USBError:
                        continue
            except (usb.core.USBError, ValueError):  # ValueError: endpoint not in the interface's active setting
                probe_reply = ""
            if "OK" in probe_reply:
                self._note_new_message_notice(probe_reply)  # announcements already waiting there
                self.notification_in_endpoint = in_endpoint
                self.notification_interface_number = interface.bInterfaceNumber
                return
            usb.util.release_interface(self.usb_device, interface.bInterfaceNumber)

    def _read_notification_port(self, timeout_milliseconds):
        """Text waiting on the second AT port ('' if none), with any new-SMS announcement remembered."""
        if self.notification_in_endpoint is None:
            return ""
        try:
            received_text = bytes(self.notification_in_endpoint.read(512, timeout=timeout_milliseconds)).decode(errors="ignore")
        except usb.core.USBError:
            return ""
        self._note_new_message_notice(received_text)
        return received_text

    def enable_new_message_notifications(self):
        """Ask the dongle to announce each new SMS. Sets `notifications_enabled`; if no setting is accepted the monitor
        falls back to reading the SIM on a timer, as before."""
        self.send_at_command('AT+CPMS="SM","SM","SM"')  # announcements name the SIM ("SM"), the storage the inbox reads
        for notification_settings in NEW_MESSAGE_NOTIFICATION_SETTINGS:
            settings_reply = self.send_at_command(f"AT+CNMI={notification_settings}")
            if "OK" in settings_reply and "ERROR" not in settings_reply:
                self.notifications_enabled = True
                return
        self.notifications_enabled = False

    def take_new_message_notice(self):
        """True if the dongle announced a new SMS since the last call."""
        notice_was_seen, self.new_message_notice_seen = self.new_message_notice_seen, False
        return notice_was_seen

    def listen_for_notifications(self, listening_seconds):
        """Wait up to `listening_seconds` for the dongle to announce something by itself. Only call it when no command is
        in flight. Returns True if a new SMS was announced (also reported by `take_new_message_notice`)."""
        deadline = time.time() + listening_seconds
        while time.time() < deadline:
            try:
                received_text = bytes(self.bulk_in_endpoint.read(512, timeout=LISTEN_READ_TIMEOUT_MILLISECONDS)).decode(errors="ignore")
            except usb.core.USBError:
                received_text = ""  # nothing said: the normal case
            self._note_new_message_notice(received_text)
            self._read_notification_port(LISTEN_READ_TIMEOUT_MILLISECONDS // 2)
            if self.new_message_notice_seen:
                break
        return self.take_new_message_notice()

    def _note_new_message_notice(self, received_text):
        if NEW_MESSAGE_NOTICE_PATTERN.search(received_text):
            self.new_message_notice_seen = True

    def close(self):
        try:
            if self.claimed_interface_number is not None:
                usb.util.release_interface(self.usb_device, self.claimed_interface_number)
            if self.notification_interface_number is not None:
                usb.util.release_interface(self.usb_device, self.notification_interface_number)
            usb.util.dispose_resources(self.usb_device)
        except usb.core.USBError:
            pass

    @staticmethod
    def _find_bulk_endpoint(interface, direction):
        return usb.util.find_descriptor(
            interface,
            custom_match=lambda endpoint: usb.util.endpoint_direction(endpoint.bEndpointAddress) == direction
            and usb.util.endpoint_type(endpoint.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK,
        )

    def _discard_pending_input(self):
        """Drop unsolicited lines (^RSSI, ^BOOT...) so they don't pollute the next reply. A new-SMS announcement among them
        is remembered first, so sending a command never makes it get lost."""
        discarded_text = ""
        while True:
            try:
                discarded_text += bytes(self.bulk_in_endpoint.read(512, timeout=50)).decode(errors="ignore")
            except usb.core.USBError:
                self._note_new_message_notice(discarded_text)
                while self._read_notification_port(10):  # keep the second port from filling up
                    pass
                return

    def _write_and_read(self, raw_text, wait_seconds, is_reply_complete):
        self._discard_pending_input()
        self.bulk_out_endpoint.write(raw_text.encode(), timeout=2000)
        received_bytes, deadline = b"", time.time() + wait_seconds
        while time.time() < deadline:
            try:
                received_bytes += bytes(self.bulk_in_endpoint.read(512, timeout=200))
            except usb.core.USBError:
                continue
            if is_reply_complete(received_bytes.decode(errors="ignore")):
                break
        reply_text = received_bytes.decode(errors="ignore")
        self._note_new_message_notice(reply_text)  # an announcement can arrive in the middle of a reply (e.g. during a send)
        return reply_text.strip()

    def send_at_command(self, command_text, wait_seconds=2.0):
        return self._write_and_read(command_text + "\r", wait_seconds, is_final_reply)

    def send_text_message(self, destination_number, message_text):
        """Send a message, as one SMS or several (accents, long text). Raises SendRejected if it certainly failed,
        SendOutcomeUnknown if the dongle may or may not have sent it (also when only some parts of a long message went out:
        it is then never retried, the recipient must be checked). A plain ASCII SMS keeps the text-mode path verified
        with this dongle; anything else is sent in PDU mode."""
        sms_plan = plan_sms(message_text)
        if sms_plan.is_too_long:
            raise SendRejected(f"Message trop long : plus de {MAX_PARTS_PER_MESSAGE} SMS")
        if is_plain_single_sms(sms_plan):
            self.send_at_command("AT+CMGF=1")
            return self._submit_sms(f'AT+CMGS="{destination_number}"', message_text)
        return self._send_in_pdu_mode(destination_number, sms_plan)

    def _send_in_pdu_mode(self, destination_number, sms_plan):
        if "OK" not in self.send_at_command("AT+CMGF=0"):
            raise SendRejected("Le dongle refuse le mode PDU")
        submit_pdus = build_submit_pdus(destination_number, sms_plan)
        last_reply = ""
        for part_index, (tpdu_length, pdu_hex) in enumerate(submit_pdus):
            part_label = f"partie {part_index + 1}/{len(submit_pdus)}"
            try:
                last_reply = self._submit_sms(f"AT+CMGS={tpdu_length}", pdu_hex)
            except (SendRejected, SendOutcomeUnknown) as failure:
                if part_index == 0:
                    raise  # nothing went out yet: the failure keeps its meaning
                raise SendOutcomeUnknown(
                    f"{part_label} non confirmée ({failure}) alors que {part_index} partie(s) déjà envoyée(s) : "
                    "vérifiez auprès du destinataire"
                )
            if part_index + 1 < len(submit_pdus):
                time.sleep(PAUSE_BETWEEN_PARTS_SECONDS)
        return last_reply

    def _submit_sms(self, send_command, body):
        """`AT+CMGS` then the `>` prompt, then the body (text, or PDU hex) ended by Ctrl+Z."""
        prompt_reply = self._write_and_read(
            send_command + "\r",
            5.0,
            lambda reply: reply.rstrip().endswith(">") or is_final_reply(reply),
        )
        if not prompt_reply.endswith(">"):
            self._write_and_read("\x1b", 1.0, is_final_reply)  # ESC cancels a half-open send
            raise SendRejected(prompt_reply or "Le dongle n'a pas demandé le texte du message")
        try:
            final_reply = self._write_and_read(body + "\x1a", SEND_TIMEOUT_SECONDS, is_final_reply)
        except usb.core.USBError as usb_error:
            raise SendOutcomeUnknown(f"Erreur USB pendant l'envoi : {usb_error}")
        if "+CMGS:" in final_reply:
            return final_reply
        if "ERROR" in final_reply:
            raise SendRejected(final_reply)
        raise SendOutcomeUnknown("Aucune confirmation du dongle")

    def read_stored_sms_parts(self):
        """Every received message part currently on the SIM, decoded from PDU mode (the only mode that says which part of a
        long message each one is). Raises RuntimeError if the dongle refuses. Sending switches back to text mode by itself."""
        self.send_at_command("AT+CMGF=0")
        self.send_at_command('AT+CPMS="SM","SM","SM"')
        list_reply = self._write_and_read("AT+CMGL=4\r", LIST_MESSAGES_TIMEOUT_SECONDS, is_end_of_message_list)
        if not is_end_of_message_list(list_reply):
            raise RuntimeError("Lecture des messages de la SIM incomplète")
        if "+CMS ERROR" in list_reply:
            raise RuntimeError(f"La SIM a refusé la lecture des messages : {list_reply}")
        return parse_pdu_list_reply(list_reply)

    def delete_stored_message(self, sim_index):
        """Free one SIM slot. Returns True when the dongle confirmed."""
        return "OK" in self.send_at_command(f"AT+CMGD={sim_index}", wait_seconds=5.0)

    def send_ussd_request(self, request_text, wait_seconds=USSD_REPLY_TIMEOUT_SECONDS):
        """Send one USSD string (a code like `*222#`, or the answer to a menu) and return the operator's decoded UssdReply.
        The reply arrives asynchronously, seconds after the immediate 'OK'. The session is left as the network left it:
        `reply.is_session_open` says whether a menu is waiting for the next answer.
        Raises RuntimeError (French message) if the dongle or network gives no usable reply, ValueError for unsupported characters."""
        request_reply = self._write_and_read(
            f'AT+CUSD=1,"{encode_ussd_request(request_text)}",15\r',
            wait_seconds,
            lambda reply: USSD_REPLY_LINE_PATTERN.search(reply) is not None or "ERROR" in reply,
        )
        reply_match = USSD_REPLY_LINE_PATTERN.search(request_reply + "\n")  # the reply text comes back stripped
        if reply_match is None:
            if "ERROR" in request_reply:
                raise RuntimeError(f"Le dongle a refusé le code USSD : {request_reply}")
            raise RuntimeError("Pas de réponse de l'opérateur au code USSD")
        ussd_status = int(reply_match.group("status"))
        if ussd_status == USSD_STATUS_NOT_SUPPORTED:
            raise RuntimeError("Code USSD non pris en charge par le réseau")
        if ussd_status == USSD_STATUS_NETWORK_TIMEOUT:
            raise RuntimeError("Le réseau n'a pas répondu à temps (session USSD expirée)")
        reply_hex = reply_match.group("reply_hex")
        try:
            reply_text = decode_ussd_reply(reply_hex, int(reply_match.group("data_coding_scheme") or 15)) if reply_hex else ""
        except ValueError:
            raise RuntimeError("Réponse USSD illisible")
        return UssdReply(ussd_status, reply_text)

    def close_ussd_session(self):
        self.send_at_command("AT+CUSD=2")

    def run_ussd_code(self, ussd_code, wait_seconds=USSD_REPLY_TIMEOUT_SECONDS):
        """Dial a code that only reads information and return the reply text. Never answers a menu: if one opens, it is closed.
        Raises RuntimeError (French message) if there is no usable reply."""
        self.close_ussd_session()  # close any session left open, so ours starts clean
        ussd_reply = self.send_ussd_request(ussd_code, wait_seconds)
        if ussd_reply.is_session_open:
            self.close_ussd_session()
        if not ussd_reply.text:
            raise RuntimeError("Réponse USSD vide")
        return ussd_reply.text

    def read_identity(self):
        imei_match = re.search(r"\d{15}", self.send_at_command("AT+CGSN"))
        return imei_match.group(0) if imei_match else None

    def read_live_status(self, status):
        """Fill the changing fields; raises if the dongle stops answering."""
        signal_reply = self.send_at_command("AT+CSQ")
        signal_match = re.search(r"\+CSQ:\s*(\d+),", signal_reply)
        if signal_match is None:
            raise RuntimeError("Pas de réponse à AT+CSQ")
        status.signal_dbm = signal_value_to_dbm(int(signal_match.group(1)))
        status.signal_bars = dbm_to_bars(status.signal_dbm)

        sim_match = re.search(r"\+CPIN:\s*(.+)", self.send_at_command("AT+CPIN?"))
        status.sim_status = sim_match.group(1).strip() if sim_match else "Inconnu"

        registration_match = re.search(r"\+CREG:\s*\d+,(\d+)", self.send_at_command("AT+CREG?"))
        registration_labels = {"0": "Non enregistré", "1": "Réseau local", "2": "Recherche", "3": "Refusé", "5": "Itinérance"}
        status.network_registration = (
            registration_labels.get(registration_match.group(1), "Inconnu") if registration_match else "Inconnu"
        )

        operator_match = re.search(r'\+COPS:\s*\d+,\d+,"([^"]*)"', self.send_at_command("AT+COPS?"))
        status.operator_name = operator_match.group(1) if operator_match else None
        if status.operator_network_code is None and operator_match:
            self._read_operator_network_code(status)

    def _read_operator_network_code(self, status):
        """The numeric operator id is far more reliable than the name. Switching the display format does not touch the registration."""
        self.send_at_command("AT+COPS=3,2")
        code_match = re.search(r'\+COPS:\s*\d+,2,"(\d{5,6})"', self.send_at_command("AT+COPS?"))
        self.send_at_command("AT+COPS=3,0")
        status.operator_network_code = code_match.group(1) if code_match else None
