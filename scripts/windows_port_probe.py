"""Find which COM port of a Huawei dongle answers AT commands (Windows experiment, also runs on macOS/Linux).

Standalone: needs only `pyserial` (pip install pyserial), imports nothing from the app.
READ-ONLY: it sends only harmless queries (AT, ATI, AT+CGSN, AT+CPIN?, AT+CSQ, AT+CREG?, AT+COPS?, AT+CMGF=?,
AT+CNMI=?, AT+CSCS?). It never sends an SMS, never dials a USSD code and never changes a setting.

Close Huawei "Internet Everywhere" / Mobile Partner and SMS Caster first: only one program can open a COM port.

Usage:
    python windows_port_probe.py              # probe every Huawei port (vendor 12d1), or all ports if none is Huawei
    python windows_port_probe.py --all-ports  # probe every serial port
    python windows_port_probe.py --port COM5  # probe only this port

The report is printed and also saved next to the script as port_probe_report.txt: send that file back.
"""
import argparse
import platform
import sys
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial is missing. Install it with:  pip install pyserial")

HUAWEI_USB_VENDOR_ID = 0x12D1
BAUD_RATE = 115200  # ignored by USB virtual ports, but required by the API
REPLY_TIMEOUT_SECONDS = 3.0
UNSOLICITED_LISTEN_SECONDS = 6.0
REPORT_FILE_PATH = Path(__file__).with_name("port_probe_report.txt")

HARMLESS_QUERIES = [
    ("AT", "basic liveness"),
    ("ATI", "manufacturer / model"),
    ("AT+CGSN", "IMEI (identifies the dongle)"),
    ("AT+CPIN?", "SIM state"),
    ("AT+CSQ", "signal strength"),
    ("AT+CREG?", "network registration"),
    ("AT+COPS?", "operator"),
    ("AT+CSCS?", "current character set"),
    ("AT+CMGF=?", "SMS formats supported (0 = PDU, 1 = text)"),
    ("AT+CNMI=?", "new-message notification modes supported"),
]

report_lines = []


def log(line=""):
    print(line)
    report_lines.append(line)


def read_until_final_reply(open_port, timeout_seconds):
    """Collect bytes until a final result code (OK / ERROR / +CME / +CMS ERROR) or the timeout."""
    deadline = time.monotonic() + timeout_seconds
    received = b""
    while time.monotonic() < deadline:
        chunk = open_port.read(open_port.in_waiting or 1)
        if chunk:
            received += chunk
            decoded = received.decode("latin-1")
            if "\r\nOK\r\n" in decoded or "ERROR" in decoded:
                break
    return received.decode("latin-1", errors="replace")


def send_query(open_port, command):
    open_port.reset_input_buffer()
    started_at = time.monotonic()
    open_port.write((command + "\r").encode("ascii"))
    reply = read_until_final_reply(open_port, REPLY_TIMEOUT_SECONDS)
    return reply, time.monotonic() - started_at


def probe_port(port_name):
    """Returns True when the port answered AT with OK."""
    log(f"\n=== {port_name} ===")
    try:
        open_port = serial.Serial(port_name, BAUD_RATE, timeout=0.2, write_timeout=2)
    except (serial.SerialException, OSError) as open_error:
        log(f"  cannot open: {open_error}")
        log("  (busy? close Internet Everywhere / Mobile Partner / SMS Caster, then retry)")
        return False

    answered_ok = False
    with open_port:
        for command, purpose in HARMLESS_QUERIES:
            reply, elapsed_seconds = send_query(open_port, command)
            compact_reply = " | ".join(part for part in reply.replace("\r", "").split("\n") if part) or "(no reply)"
            log(f"  {command:<10} {elapsed_seconds:4.1f}s  {compact_reply}   [{purpose}]")
            if command == "AT":
                answered_ok = "OK" in reply
                if not answered_ok:
                    log("  -> no OK to AT: not an AT port (or the modem is busy). Skipping the other queries.")
                    break
        if answered_ok:
            log(f"  listening {UNSOLICITED_LISTEN_SECONDS:.0f}s for unsolicited lines (^RSSI, ^HCSQ, +CMTI...)")
            listen_deadline = time.monotonic() + UNSOLICITED_LISTEN_SECONDS
            heard = b""
            while time.monotonic() < listen_deadline:
                heard += open_port.read(open_port.in_waiting or 1)
            heard_text = heard.decode("latin-1", errors="replace").strip()
            log(f"  unsolicited: {heard_text.replace(chr(13), '').replace(chr(10), ' | ') or '(none)'}")
    return answered_ok


def main():
    argument_parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    argument_parser.add_argument("--all-ports", action="store_true", help="probe every serial port, not only Huawei ones")
    argument_parser.add_argument("--port", help="probe only this port, e.g. COM5")
    arguments = argument_parser.parse_args()

    log(f"Port probe — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"System: {platform.platform()} | Python {platform.python_version()} | pyserial {serial.__version__}")

    all_ports = sorted(list_ports.comports(), key=lambda port_info: port_info.device)
    log(f"\nSerial ports found: {len(all_ports)}")
    for port_info in all_ports:
        vendor_text = f"{port_info.vid:04x}" if port_info.vid is not None else "----"
        product_text = f"{port_info.pid:04x}" if port_info.pid is not None else "----"
        log(f"  {port_info.device:<8} usb {vendor_text}:{product_text}  {port_info.description}  [{port_info.hwid}]")

    if arguments.port:
        ports_to_probe = [arguments.port]
    elif arguments.all_ports:
        ports_to_probe = [port_info.device for port_info in all_ports]
    else:
        huawei_ports = [port_info.device for port_info in all_ports if port_info.vid == HUAWEI_USB_VENDOR_ID]
        if huawei_ports:
            ports_to_probe = huawei_ports
        else:
            log("\nNo port with Huawei vendor id 12d1: probing every port instead.")
            ports_to_probe = [port_info.device for port_info in all_ports]

    if not ports_to_probe:
        log("\nNothing to probe: no serial port at all. Is the dongle plugged in and the Huawei driver installed?")
    answering_ports = [port_name for port_name in ports_to_probe if probe_port(port_name)]

    log("\n=== Summary ===")
    log(f"Ports that answered AT with OK: {', '.join(answering_ports) or 'none'}")

    REPORT_FILE_PATH.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"\nReport saved to {REPORT_FILE_PATH}")


if __name__ == "__main__":
    main()
