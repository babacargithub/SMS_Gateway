#!/usr/bin/env python3
"""Talk to a Huawei dongle (12d1:151d) over raw USB -- no macOS driver needed.

Interactive AT shell:  sudo ~/usbenv/bin/python huawei_at.py
Send an SMS:           sudo ~/usbenv/bin/python huawei_at.py send +221XXXXXXXXX "Hello from Mac"
Watch incoming calls:  ~/usbenv/bin/python huawei_at.py listen [seconds]   (then call the SIM from another phone)
"""
import sys
import time
import usb.core
import usb.util
import usb.backend.libusb1

VID, PID = 0x12d1, 0x151d

backend = usb.backend.libusb1.get_backend(
    find_library=lambda _: "/opt/homebrew/lib/libusb-1.0.dylib"
) or usb.backend.libusb1.get_backend()
if backend is None:
    sys.exit("libusb not found. Run: brew install libusb")

dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
if dev is None:
    sys.exit("Dongle 12d1:151d not found. If it was re-plugged, run huawei_switch.py first.")

try:
    cfg = dev.get_active_configuration()
except usb.core.USBError:
    dev.set_configuration()
    cfg = dev.get_active_configuration()


def bulk_endpoints(intf):
    def pick(direction):
        return usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == direction
            and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK,
        )
    return pick(usb.util.ENDPOINT_OUT), pick(usb.util.ENDPOINT_IN)


def read_response(ep_in, wait):
    buf, end = b"", time.time() + wait
    while time.time() < end:
        try:
            buf += bytes(ep_in.read(512, timeout=200))
            text = buf.decode(errors="ignore")
            if text.rstrip().endswith(("OK", "ERROR", ">")) or "+CMS ERROR" in text or "+CME ERROR" in text:
                break
        except usb.core.USBError:
            continue  # timeout: keep waiting until `wait` expires
    return buf.decode(errors="ignore").strip()


def send(port, text, wait=2.0):
    ep_out, ep_in = port
    ep_out.write(text.encode())
    return read_response(ep_in, wait)


def find_at_port():
    for intf in cfg:
        if intf.bInterfaceClass != 0xFF:  # AT ports are vendor-specific
            continue
        ep_out, ep_in = bulk_endpoints(intf)
        if ep_out is None or ep_in is None:
            continue
        try:
            usb.util.claim_interface(dev, intf.bInterfaceNumber)
        except usb.core.USBError:
            continue
        if "OK" in send((ep_out, ep_in), "AT\r", 1.5):
            print(f"AT port found on interface {intf.bInterfaceNumber}")
            return ep_out, ep_in
        usb.util.release_interface(dev, intf.bInterfaceNumber)
    return None


port = find_at_port()
if port is None:
    sys.exit("No interface answered 'AT'. Unplug/replug, re-run huawei_switch.py, then retry.")

def listen_for_unsolicited_lines(port, listen_seconds):
    """Turn on caller ID and status notifications, then print every line the dongle emits, as it arrives."""
    for setup_command in ("ATE0", "AT+CLIP=1", "AT^CURC=1", "AT^CVOICE?"):
        print(f"{setup_command} -> {send(port, setup_command + chr(13))!r}")
    print(f"\nListening for {listen_seconds} s. Call the SIM now from another phone...\n")
    _, ep_in = port
    end_time = time.time() + listen_seconds
    while time.time() < end_time:
        try:
            received = bytes(ep_in.read(512, timeout=500)).decode(errors="ignore").strip()
        except usb.core.USBError:
            continue  # read timeout: nothing arrived
        if received:
            print(f"[{time.strftime('%H:%M:%S')}] {received!r}")
    print("Done listening.")


if len(sys.argv) >= 2 and sys.argv[1] == "listen":
    listen_for_unsolicited_lines(port, int(sys.argv[2]) if len(sys.argv) >= 3 else 60)
elif len(sys.argv) >= 4 and sys.argv[1] == "send":
    number, message = sys.argv[2], " ".join(sys.argv[3:])
    print(send(port, "AT+CMGF=1\r"))
    print(send(port, f'AT+CMGS="{number}"\r', 3.0))  # expect the '>' prompt
    print(send(port, message + "\x1a", 15.0))          # Ctrl+Z sends the SMS
else:
    print("Type AT commands (e.g. AT+CSQ). Type 'exit' to quit.")
    while True:
        line = input("AT> ").strip()
        if line.lower() in ("exit", "quit"):
            break
        if line:
            print(send(port, line + "\r"))
