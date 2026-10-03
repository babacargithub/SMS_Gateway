#!/usr/bin/env python3
"""Switch a Huawei USB dongle (12d1:14fe) from CD-ROM mode to modem mode.
Replaces usb_modeswitch, which is not available on Homebrew.
Run with: sudo ~/usbenv/bin/python huawei_switch.py
"""
import sys
import usb.core
import usb.util
import usb.backend.libusb1

VID, PID = 0x12d1, 0x14fe
# Standard Huawei mode-switch message used by usb_modeswitch for 12d1:14fe
MSG = bytes.fromhex("55534243123456780000000000000011062000000100000000000000000000")

# Homebrew libusb location on Apple Silicon (falls back to default search)
backend = usb.backend.libusb1.get_backend(
    find_library=lambda _: "/opt/homebrew/lib/libusb-1.0.dylib"
) or usb.backend.libusb1.get_backend()
if backend is None:
    sys.exit("libusb not found. Run: brew install libusb")

dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
if dev is None:
    sys.exit("Dongle 12d1:14fe not found (unplugged, or already switched?)")

try:
    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)
except (NotImplementedError, usb.core.USBError):
    pass  # macOS may not support detaching; continue anyway

intf = dev.get_active_configuration()[(0, 0)]
ep_out = usb.util.find_descriptor(
    intf,
    custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
    == usb.util.ENDPOINT_OUT,
)
if ep_out is None:
    sys.exit("No OUT endpoint found on interface 0")

try:
    usb.util.claim_interface(dev, 0)
    ep_out.write(MSG)
    print("Switch command sent. Wait ~10 seconds, then re-check with system_profiler.")
except usb.core.USBError as e:
    sys.exit(f"USB error: {e}\nmacOS is probably still holding the disk. "
             "Eject it (diskutil eject disk8) and retry.")
