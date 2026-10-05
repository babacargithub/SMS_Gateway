#!/usr/bin/env python3
"""Send ONE test SMS to the owner's own number (env TEST_NUMBER) with the app's real encoder, to compare what each kind of
text costs. Close the app first (only one process can own the dongle).

    TEST_NUMBER=+221XXXXXXXXX ~/usbenv/bin/python scripts/send_test_sms.py "Réservé à très bientôt é è à ù"

Prints how the text will be sent (7-bit or Unicode, how many SMS) and asks before sending. Note your balance before and
after (`*222#` / `#123#` in the SIM screen) to see what the operator really billed."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.sms_encoding import characters_forcing_unicode, plan_sms
from common.sms_text import normalize_senegal_number
from devices.dongle import DongleConnection, find_huawei_devices, load_usb_backend

MODEM_MODE_PRODUCT_ID = 0x151D

test_number = normalize_senegal_number(os.environ.get("TEST_NUMBER", ""))
if test_number is None or len(sys.argv) < 2:
    sys.exit('Usage: TEST_NUMBER=+221XXXXXXXXX python scripts/send_test_sms.py "text"')
message_text = " ".join(sys.argv[1:])

sms_plan = plan_sms(message_text)
print(f"Vers {test_number} : {len(message_text)} caractères -> {sms_plan.part_count} SMS, "
      f"{'Unicode' if sms_plan.is_unicode else '7 bits'}")
if sms_plan.is_unicode:
    print("Caractères hors alphabet 7 bits :", " ".join(characters_forcing_unicode(message_text)))
if input("Envoyer vraiment ? (o/N) ").strip().lower() != "o":
    sys.exit("Annulé.")

dongles = find_huawei_devices(load_usb_backend(), MODEM_MODE_PRODUCT_ID)
if not dongles:
    sys.exit("Aucun dongle en mode modem.")
connection = DongleConnection(dongles[0])
connection.open()
try:
    print("Réponse du dongle :", connection.send_text_message(test_number, message_text))
finally:
    connection.close()
