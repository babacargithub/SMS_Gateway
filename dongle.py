"""Device layer: raw-USB access to Huawei dongles. No UI code in here."""
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from received_sms_parser import parse_sms_list_reply

import usb.backend.libusb1
import usb.core
import usb.util

HUAWEI_VENDOR_ID = 0x12D1
CDROM_PRODUCT_ID = 0x14FE
MODEM_PRODUCT_ID = 0x151D
MODE_SWITCH_MESSAGE = bytes.fromhex("55534243123456780000000000000011062000000100000000000000000000")
CDROM_VOLUME_PATH = "/Volumes/Internet Everywhere"
LIBUSB_LIBRARY_PATH = "/opt/homebrew/lib/libusb-1.0.dylib"
VENDOR_SPECIFIC_INTERFACE_CLASS = 0xFF

SEND_TIMEOUT_SECONDS = 30
LIST_MESSAGES_TIMEOUT_SECONDS = 15

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
class DongleStatus:
    usb_location: str
    state: str
    imei: Optional[str] = None
    operator_name: Optional[str] = None
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


def eject_cdrom_volume():
    """macOS mounts the virtual CD-ROM and holds it; it must be ejected before the switch."""
    subprocess.run(
        ["diskutil", "eject", CDROM_VOLUME_PATH],
        capture_output=True,
        timeout=15,
    )


def switch_to_modem_mode(cdrom_usb_device):
    eject_cdrom_volume()
    interface_zero = cdrom_usb_device.get_active_configuration()[(0, 0)]
    bulk_out_endpoint = usb.util.find_descriptor(
        interface_zero,
        custom_match=lambda endpoint: usb.util.endpoint_direction(endpoint.bEndpointAddress)
        == usb.util.ENDPOINT_OUT,
    )
    if bulk_out_endpoint is None:
        raise RuntimeError("Aucun point de sortie sur l'interface 0 du périphérique CD-ROM")
    usb.util.claim_interface(cdrom_usb_device, 0)
    bulk_out_endpoint.write(MODE_SWITCH_MESSAGE)


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
                return
            usb.util.release_interface(self.usb_device, interface.bInterfaceNumber)
        raise RuntimeError("Aucune interface n'a répondu à 'AT'")

    def close(self):
        try:
            if self.claimed_interface_number is not None:
                usb.util.release_interface(self.usb_device, self.claimed_interface_number)
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
        """Drop unsolicited lines (^RSSI, ^BOOT...) so they don't pollute the next reply."""
        while True:
            try:
                self.bulk_in_endpoint.read(512, timeout=50)
            except usb.core.USBError:
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
        return received_bytes.decode(errors="ignore").strip()

    def send_at_command(self, command_text, wait_seconds=2.0):
        return self._write_and_read(command_text + "\r", wait_seconds, is_final_reply)

    def send_text_message(self, destination_number, message_text):
        """Send one text-mode SMS. Raises SendRejected if it certainly failed,
        SendOutcomeUnknown if the dongle may or may not have sent it."""
        self.send_at_command("AT+CMGF=1")
        prompt_reply = self._write_and_read(
            f'AT+CMGS="{destination_number}"\r',
            5.0,
            lambda reply: reply.rstrip().endswith(">") or is_final_reply(reply),
        )
        if not prompt_reply.endswith(">"):
            self._write_and_read("\x1b", 1.0, is_final_reply)  # ESC cancels a half-open send
            raise SendRejected(prompt_reply or "Le dongle n'a pas demandé le texte du message")
        try:
            final_reply = self._write_and_read(message_text + "\x1a", SEND_TIMEOUT_SECONDS, is_final_reply)
        except usb.core.USBError as usb_error:
            raise SendOutcomeUnknown(f"Erreur USB pendant l'envoi : {usb_error}")
        if "+CMGS:" in final_reply:
            return final_reply
        if "ERROR" in final_reply:
            raise SendRejected(final_reply)
        raise SendOutcomeUnknown("Aucune confirmation du dongle")

    def read_stored_text_messages(self):
        """Every received message currently on the SIM (text mode, decoded). Raises RuntimeError if the dongle refuses."""
        self.send_at_command("AT+CMGF=1")
        self.send_at_command("AT+CSDH=1")  # shows the data coding scheme, so UCS2 bodies can be recognised
        self.send_at_command('AT+CPMS="SM","SM","SM"')
        list_reply = self._write_and_read('AT+CMGL="ALL"\r', LIST_MESSAGES_TIMEOUT_SECONDS, is_end_of_message_list)
        if not is_end_of_message_list(list_reply):
            raise RuntimeError("Lecture des messages de la SIM incomplète")
        if "+CMS ERROR" in list_reply:
            raise RuntimeError(f"La SIM a refusé la lecture des messages : {list_reply}")
        return parse_sms_list_reply(list_reply)

    def delete_stored_message(self, sim_index):
        """Free one SIM slot. Returns True when the dongle confirmed."""
        return "OK" in self.send_at_command(f"AT+CMGD={sim_index}", wait_seconds=5.0)

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
