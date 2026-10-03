#!/usr/bin/env python3
"""Root helper for Huawei dongles: CD-ROM mode (12d1:14fe) -> modem mode (12d1:151d).

The switch needs root, and macOS also refuses to let a process that is not part of a login session touch the dongle's
storage interface (kernel: 'System Policy: deny iokit-open-service IOUSBHostInterface'), even as root. So the SMS Gateway
app (started from a terminal, i.e. inside the login session) runs this as `sudo -n <python> <this script> --switch-now`,
allowed by ONE passwordless sudoers rule (see install_switcher.sh). The command takes NO argument, so the rule cannot be
bent: it switches whatever dongles are in CD-ROM mode right now, and nothing else.

Keep this file self-contained: it is copied to a root-owned folder. Constants must stay in sync with devices/dongle.py."""
import logging
import os
import subprocess
import sys
import time

import usb.backend.libusb1
import usb.core
import usb.util

HUAWEI_VENDOR_ID = 0x12D1
CDROM_PRODUCT_ID = 0x14FE
MODE_SWITCH_MESSAGE = bytes.fromhex("55534243123456780000000000000011062000000100000000000000000000")
CDROM_VOLUME_PATH = "/Volumes/Internet Everywhere"
LIBUSB_LIBRARY_PATH = "/opt/homebrew/lib/libusb-1.0.dylib"

EJECT_TIMEOUT_SECONDS = 15
SETTLE_AFTER_EJECT_SECONDS = 1.5
SWITCH_NOW_ARGUMENT = "--switch-now"


def eject_cdrom_volume():
    """macOS mounts the virtual CD-ROM and holds it; it must be ejected before the switch."""
    try:
        eject_result = subprocess.run(
            ["diskutil", "eject", CDROM_VOLUME_PATH], capture_output=True, text=True, timeout=EJECT_TIMEOUT_SECONDS
        )
        logging.info("diskutil eject -> code %s %s", eject_result.returncode, (eject_result.stdout + eject_result.stderr).strip())
    except subprocess.SubprocessError:
        logging.warning("diskutil eject did not finish; trying the switch anyway")


def run_step(step_name, step_function):
    """Name the failing step in the error: libusb's own 'Other error' says nothing about where it happened."""
    try:
        return step_function()
    except (usb.core.USBError, RuntimeError, OSError) as step_error:
        raise RuntimeError(f"step « {step_name} » failed: {step_error}") from step_error


def find_cdrom_device(usb_backend, location):
    for candidate in usb.core.find(find_all=True, idVendor=HUAWEI_VENDOR_ID, idProduct=CDROM_PRODUCT_ID, backend=usb_backend):
        if f"{candidate.bus}:{candidate.address}" == location:
            return candidate
    return None


def switch_to_modem_mode(usb_backend, location):
    eject_cdrom_volume()
    time.sleep(SETTLE_AFTER_EJECT_SECONDS)
    # The eject changes the device's state on macOS: a handle found before it can be stale, so look it up again.
    cdrom_usb_device = find_cdrom_device(usb_backend, location)
    if cdrom_usb_device is None:
        raise RuntimeError("the dongle disappeared after the eject")
    try:
        if cdrom_usb_device.is_kernel_driver_active(0):
            cdrom_usb_device.detach_kernel_driver(0)
    except (NotImplementedError, usb.core.USBError):
        pass  # macOS may not support detaching; continue anyway
    interface_zero = run_step("read configuration", lambda: cdrom_usb_device.get_active_configuration()[(0, 0)])
    bulk_out_endpoint = usb.util.find_descriptor(
        interface_zero,
        custom_match=lambda endpoint: usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_OUT,
    )
    if bulk_out_endpoint is None:
        raise RuntimeError("no bulk OUT endpoint on interface 0")
    run_step("claim interface 0", lambda: usb.util.claim_interface(cdrom_usb_device, 0))
    run_step("write switch message", lambda: bulk_out_endpoint.write(MODE_SWITCH_MESSAGE))
    usb.util.dispose_resources(cdrom_usb_device)


def load_usb_backend():
    usb_backend = usb.backend.libusb1.get_backend(find_library=lambda _: LIBUSB_LIBRARY_PATH) or usb.backend.libusb1.get_backend()
    if usb_backend is None:
        sys.exit("libusb not found (brew install libusb)")
    return usb_backend


def locations_of_cdrom_dongles(usb_backend):
    return [
        f"{cdrom_device.bus}:{cdrom_device.address}"
        for cdrom_device in usb.core.find(
            find_all=True, idVendor=HUAWEI_VENDOR_ID, idProduct=CDROM_PRODUCT_ID, backend=usb_backend
        )
    ]


def switch_all_cdrom_dongles_now():
    """Root side. Exit code 0 if every dongle found was switched (or none was found), 1 otherwise."""
    usb_backend = load_usb_backend()
    every_switch_worked = True
    for location in locations_of_cdrom_dongles(usb_backend):
        try:
            switch_to_modem_mode(usb_backend, location)
            logging.info("Switch command sent to %s", location)
        except (usb.core.USBError, RuntimeError, OSError) as switch_error:
            logging.error("Switch failed at %s: %s", location, switch_error)
            every_switch_worked = False
    sys.exit(0 if every_switch_worked else 1)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    if sys.argv[1:] != [SWITCH_NOW_ARGUMENT]:
        sys.exit(f"usage: {os.path.basename(__file__)} {SWITCH_NOW_ARGUMENT}")
    switch_all_cdrom_dongles_now()


if __name__ == "__main__":
    main()
