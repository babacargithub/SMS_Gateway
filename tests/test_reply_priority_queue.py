import tempfile
import unittest
from pathlib import Path

from outbox import outbox_store
from outbox.message_sender import DONGLE_UNAVAILABLE
from outbox.outbox_store import OutboxStore
from outbox.sending_worker import SendingWorker


class FakeDraftMessage:
    def __init__(self, phone_number):
        self.list_name, self.recipient_name, self.phone_number, self.message_text = "L", "", phone_number, "Campagne"


class FakeSendResult:
    def __init__(self, final_state, error_detail=""):
        self.final_state, self.error_detail = final_state, error_detail


class RecordingSender:
    def __init__(self, on_send=None, final_state=outbox_store.SENT):
        self.sent_numbers = []
        self.on_send = on_send
        self.final_state = final_state

    def send(self, phone_number, message_text):
        self.sent_numbers.append(phone_number)
        if self.on_send:
            self.on_send(len(self.sent_numbers))
        return FakeSendResult(self.final_state)


class ReplyPriorityQueueTest(unittest.TestCase):
    def setUp(self):
        self.store = OutboxStore(Path(tempfile.mkdtemp()) / "test.db")
        self.store.queue_campaign("Campagne", ["L"], [FakeDraftMessage(f"+22177000000{digit}") for digit in range(3)])

    def test_reply_is_claimed_before_older_campaign_messages(self):
        self.store.queue_reply("+221771111111", "Awa", "Merci")
        claimed = self.store.claim_next_waiting_message()
        self.assertEqual(claimed.phone_number, "+221771111111")
        self.assertEqual(claimed.priority, outbox_store.REPLY_PRIORITY)

    def test_replies_keep_their_order_and_the_list_shows_sending_order(self):
        self.store.queue_reply("+221771111111", "", "Un")
        self.store.queue_reply("+221772222222", "", "Deux")
        listed = [message.phone_number for message in self.store.list_messages()]
        self.assertEqual(listed[:2], ["+221771111111", "+221772222222"])
        self.assertEqual(self.store.count_waiting_replies(), 2)

    def test_replies_only_claim_ignores_campaign_messages(self):
        self.assertIsNone(self.store.claim_next_waiting_message(replies_only=True))
        self.store.queue_reply("+221771111111", "", "Merci")
        self.assertIsNotNone(self.store.claim_next_waiting_message(replies_only=True))
        self.assertIsNone(self.store.claim_next_waiting_message(replies_only=True))
        self.assertEqual(self.store.count_by_state()[outbox_store.WAITING], 3)

    def test_reply_queued_during_a_run_goes_out_right_after_the_current_message(self):
        def queue_reply_after_first_send(sent_count):
            if sent_count == 1:
                self.store.queue_reply("+221779999999", "", "Reponse")

        sender = RecordingSender(on_send=queue_reply_after_first_send)
        SendingWorker(self.store, sender, gap_seconds_range=(0.05, 0.05)).run()
        self.assertEqual(sender.sent_numbers[0], "+221770000000")
        self.assertEqual(sender.sent_numbers[1], "+221779999999")  # the reply, before the 2nd and 3rd campaign messages
        self.assertEqual(len(sender.sent_numbers), 4)

    def test_replies_only_worker_leaves_the_campaign_alone(self):
        self.store.queue_reply("+221771111111", "", "Merci")
        sender = RecordingSender()
        SendingWorker(self.store, sender, replies_only=True).run()
        self.assertEqual(sender.sent_numbers, ["+221771111111"])
        self.assertEqual(self.store.count_by_state()[outbox_store.WAITING], 3)

    def test_replies_only_worker_stops_and_keeps_the_reply_when_no_dongle(self):
        self.store.queue_reply("+221771111111", "", "Merci")
        sender = RecordingSender(final_state=DONGLE_UNAVAILABLE)
        SendingWorker(self.store, sender, replies_only=True).run()
        self.assertEqual(sender.sent_numbers, ["+221771111111"])  # tried once, no endless loop
        self.assertEqual(self.store.count_waiting_replies(), 1)


if __name__ == "__main__":
    unittest.main()
