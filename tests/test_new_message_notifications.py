"""AT+CNMI: the inbox is read the moment the dongle announces a new SMS. Uses the real monitor thread and the real
connection code on top of a fake USB endpoint. Run: ~/usbenv/bin/python -m unittest discover tests"""
import queue
import tempfile
import time
import unittest
from pathlib import Path

import usb.core
from PySide6.QtCore import QCoreApplication

from devices import dongle
from devices.dongle_monitor import DongleMonitor
from inbox.inbox_store import InboxStore
from tests.test_inbox_reception import build_deliver_pdu

NOTICE_LINE = '\r\n+CMTI: "SM",5\r\n'


class FakeInEndpoint:
    def __init__(self):
        self.incoming = queue.Queue()

    def read(self, size, timeout=0):
        try:
            return self.incoming.get(timeout=timeout / 1000)
        except queue.Empty:
            raise usb.core.USBError("timeout")


class FakeOutEndpoint:
    """Answers AT commands like a dongle would. `sim_pdus` is what `AT+CMGL=4` lists."""

    def __init__(self, in_endpoint, accepts_notifications=True):
        self.in_endpoint, self.accepts_notifications = in_endpoint, accepts_notifications
        self.sim_pdus, self.commands = {}, []

    def write(self, raw_bytes, timeout=0):
        command = raw_bytes.decode().strip()
        self.commands.append(command)
        if command.startswith("AT+CNMI") and not self.accepts_notifications:
            reply = "\r\nERROR\r\n"
        elif command == "AT+CMGL=4":
            reply = "".join(f"\r\n+CMGL: {index},0,,40\r\n{pdu}" for index, pdu in sorted(self.sim_pdus.items())) + "\r\nOK\r\n"
        else:
            if command.startswith("AT+CMGD="):
                self.sim_pdus.pop(int(command.split("=")[1]), None)
            reply = "\r\nOK\r\n"
        self.in_endpoint.incoming.put(reply.encode())


def build_fake_connection(accepts_notifications=True):
    connection = dongle.DongleConnection(type("FakeUsbDevice", (), {"bus": 1, "address": 8})())
    connection.bulk_in_endpoint = FakeInEndpoint()
    connection.bulk_out_endpoint = FakeOutEndpoint(connection.bulk_in_endpoint, accepts_notifications)
    connection.close = lambda: None  # there is no real USB device to release
    return connection


class ConnectionNotificationTests(unittest.TestCase):
    def test_notifications_are_enabled_with_mode_2_first(self):
        connection = build_fake_connection()
        connection.enable_new_message_notifications()
        self.assertTrue(connection.notifications_enabled)
        self.assertIn("AT+CNMI=2,1,0,0,0", connection.bulk_out_endpoint.commands)

    def test_a_dongle_that_refuses_cnmi_falls_back_to_polling(self):
        connection = build_fake_connection(accepts_notifications=False)
        connection.enable_new_message_notifications()
        self.assertFalse(connection.notifications_enabled)
        self.assertEqual(connection.bulk_out_endpoint.commands.count("AT+CNMI=2,1,0,0,0") + connection.bulk_out_endpoint.commands.count("AT+CNMI=1,1,0,0,0"), 2)

    def test_listening_returns_at_once_on_an_announcement(self):
        connection = build_fake_connection()
        connection.bulk_in_endpoint.incoming.put(NOTICE_LINE.encode())
        started_at = time.time()
        self.assertTrue(connection.listen_for_notifications(2.0))
        self.assertLess(time.time() - started_at, 0.5)

    def test_listening_ignores_other_unsolicited_lines(self):
        connection = build_fake_connection()
        connection.bulk_in_endpoint.incoming.put(b"\r\n^RSSI:18\r\n")
        self.assertFalse(connection.listen_for_notifications(0.3))

    def test_an_announcement_arriving_just_before_a_command_is_not_lost(self):
        connection = build_fake_connection()
        connection.bulk_in_endpoint.incoming.put(NOTICE_LINE.encode())   # thrown away as noise by the next command...
        connection.send_at_command("AT+CSQ")
        self.assertTrue(connection.take_new_message_notice())            # ...but remembered first
        self.assertFalse(connection.take_new_message_notice())           # and reported only once


class MonitorNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core_application = QCoreApplication.instance() or QCoreApplication([])

    def start_monitor(self, connection):
        inbox_store = InboxStore(Path(tempfile.mkdtemp()) / "inbox.db")
        monitor = DongleMonitor(inbox_store)
        monitor._scan_once = lambda usb_backend: None          # no real USB in this test
        monitor.inbox_messages_received = type("FakeSignal", (), {"emit": lambda self, count: None})()
        monitor.open_connections["1:8"] = connection
        monitor.current_statuses["1:8"] = dongle.DongleStatus("1:8", dongle.STATE_READY)
        original_load = dongle.load_usb_backend
        dongle.load_usb_backend = lambda: object()
        self.addCleanup(setattr, dongle, "load_usb_backend", original_load)
        monitor.start()
        self.addCleanup(lambda: (monitor.stop(), monitor.wait(5000)))
        return monitor, inbox_store

    def wait_until(self, condition, timeout_seconds):
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if condition():
                return True
            time.sleep(0.05)
        return False

    def test_a_message_is_stored_within_a_second_of_its_announcement_not_after_the_safety_timer(self):
        connection = build_fake_connection()
        connection.enable_new_message_notifications()
        monitor, inbox_store = self.start_monitor(connection)
        time.sleep(1.0)                                              # startup read is done; the monitor is now just listening
        self.assertEqual(inbox_store.list_messages(), [])
        stamp = time.strftime("%y%m%d%H%M%S") + "00"
        connection.bulk_out_endpoint.sim_pdus[5] = build_deliver_pdu("221771234567", "Bonjour", timestamp_text=stamp)
        announced_at = time.time()
        connection.bulk_in_endpoint.incoming.put(NOTICE_LINE.encode())
        self.assertTrue(self.wait_until(lambda: len(inbox_store.list_messages()) == 1, 3.0), "message not stored")
        self.assertLess(time.time() - announced_at, 1.5)
        self.assertEqual(inbox_store.list_messages()[0].message_text, "Bonjour")
        self.assertTrue(self.wait_until(lambda: 5 not in connection.bulk_out_endpoint.sim_pdus, 2.0))   # slot freed
        self.assertGreater(monitor._inbox_check_interval_seconds(), 10)   # announcements on: the timer is only a safety net

    def test_without_announcement_support_the_sim_is_polled_every_few_seconds(self):
        connection = build_fake_connection(accepts_notifications=False)
        connection.enable_new_message_notifications()
        monitor, inbox_store = self.start_monitor(connection)
        time.sleep(0.7)
        stamp = time.strftime("%y%m%d%H%M%S") + "00"
        connection.bulk_out_endpoint.sim_pdus[6] = build_deliver_pdu("221771234567", "Sans notification", timestamp_text=stamp)
        self.assertTrue(self.wait_until(lambda: len(inbox_store.list_messages()) == 1, 6.0), "polling did not find the message")
        self.assertLessEqual(monitor._inbox_check_interval_seconds(), 3)

    def test_a_request_from_the_ui_interrupts_the_listening_at_once(self):
        connection = build_fake_connection()
        connection.enable_new_message_notifications()
        monitor, _ = self.start_monitor(connection)
        time.sleep(0.8)
        send_request = monitor.submit_text_message("+221771234567", "test")
        send_request.finished.wait(2.0)
        self.assertTrue(send_request.finished.is_set(), "the queued send was not picked up promptly")


if __name__ == "__main__":
    unittest.main()
