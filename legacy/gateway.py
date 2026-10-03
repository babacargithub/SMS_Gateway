#!/usr/bin/env python3
"""SMS Gateway service for a Huawei USB dongle on macOS (no driver needed).

- Exposes an HTTP API (FastAPI) to queue SMS.
- Stores every message in SQLite (queue + history).
- A background worker switches the dongle to modem mode if needed,
  finds its AT interface, and sends queued messages one by one.

Run:  sudo ~/usbenv/bin/python gateway.py
Docs: http://127.0.0.1:8000/docs
"""
import os
import re
import sqlite3
import subprocess
import threading
import time
import unicodedata
from contextlib import closing

import usb.core
import usb.util
import usb.backend.libusb1
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

# ---------------------------------------------------------------- config
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API_KEY = os.environ.get("SMS_API_KEY", "change-me")
DB_PATH = os.environ.get("SMS_DB", os.path.join(BASE_DIR, "sms_gateway.db"))
HOST = os.environ.get("SMS_HOST", "127.0.0.1")   # 0.0.0.0 to allow other machines
PORT = int(os.environ.get("SMS_PORT", "8000"))
SEND_DELAY = float(os.environ.get("SMS_DELAY", "3"))  # seconds between SMS
MAX_ATTEMPTS = 3
MAX_LEN = 160  # single SMS, plain text mode

VID, STORAGE_PID, MODEM_PID = 0x12D1, 0x14FE, 0x151D
SWITCH_MSG = bytes.fromhex("55534243123456780000000000000011062000000100000000000000000000")

state = {"last_error": None, "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- modem
class SmsError(Exception):
    """The modem answered, but refused or failed the SMS."""


def _backend():
    return usb.backend.libusb1.get_backend(
        find_library=lambda _: "/opt/homebrew/lib/libusb-1.0.dylib"
    ) or usb.backend.libusb1.get_backend()


def _bulk(intf, direction):
    return usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == direction
        and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK,
    )


class Modem:
    def __init__(self):
        self.backend = _backend()
        if self.backend is None:
            raise SystemExit("libusb not found. Run: brew install libusb")
        self.dev = None
        self.port = None  # (ep_out, ep_in)
        self.lock = threading.Lock()

    @property
    def connected(self):
        return self.port is not None

    # -- low level ---------------------------------------------------
    def _read(self, ep_in, wait):
        buf, end = b"", time.time() + wait
        while time.time() < end:
            try:
                buf += bytes(ep_in.read(512, timeout=200))
                text = buf.decode(errors="ignore").rstrip()
                if text.endswith(("OK", "ERROR", ">")) or "+CMS ERROR" in text or "+CME ERROR" in text:
                    break
            except usb.core.USBTimeoutError:
                continue  # nothing yet; a real disconnect raises USBError instead
        return buf.decode(errors="ignore").strip()

    def _flush(self, ep_in):
        for _ in range(5):  # drop unsolicited lines (^RSSI, ^BOOT...)
            try:
                ep_in.read(512, timeout=50)
            except usb.core.USBTimeoutError:
                return

    def _cmd(self, text, wait=2.0, port=None):
        ep_out, ep_in = port or self.port
        self._flush(ep_in)
        ep_out.write(text.encode())
        return self._read(ep_in, wait)

    # -- connection --------------------------------------------------
    def _switch_if_needed(self):
        dev = usb.core.find(idVendor=VID, idProduct=STORAGE_PID, backend=self.backend)
        if dev is None:
            return
        log("Dongle in CD-ROM mode, switching to modem mode...")
        subprocess.run(["diskutil", "eject", "/Volumes/Internet Everywhere"],
                       capture_output=True)
        time.sleep(2)
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except (NotImplementedError, usb.core.USBError):
            pass
        intf = dev.get_active_configuration()[(0, 0)]
        ep_out = _bulk(intf, usb.util.ENDPOINT_OUT)
        usb.util.claim_interface(dev, 0)
        ep_out.write(SWITCH_MSG)
        usb.util.dispose_resources(dev)
        time.sleep(10)  # let the dongle re-enumerate

    def connect(self):
        self._switch_if_needed()
        dev = usb.core.find(idVendor=VID, idProduct=MODEM_PID, backend=self.backend)
        if dev is None:
            raise RuntimeError("Dongle not found in modem mode")
        try:
            cfg = dev.get_active_configuration()
        except usb.core.USBError:
            dev.set_configuration()
            cfg = dev.get_active_configuration()

        for intf in cfg:
            if intf.bInterfaceClass != 0xFF:
                continue
            port = (_bulk(intf, usb.util.ENDPOINT_OUT), _bulk(intf, usb.util.ENDPOINT_IN))
            if None in port:
                continue
            try:
                usb.util.claim_interface(dev, intf.bInterfaceNumber)
                if "OK" in self._cmd("AT\r", 1.5, port):
                    self.dev, self.port = dev, port
                    self._cmd("ATE0\r")       # no echo
                    self._cmd("AT+CMGF=1\r")  # text mode
                    log(f"Modem ready on interface {intf.bInterfaceNumber}")
                    return
                usb.util.release_interface(dev, intf.bInterfaceNumber)
            except usb.core.USBError:
                continue
        usb.util.dispose_resources(dev)
        raise RuntimeError("No USB interface answered 'AT'")

    def disconnect(self):
        if self.dev is not None:
            try:
                usb.util.dispose_resources(self.dev)
            except Exception:
                pass
        self.dev, self.port = None, None

    # -- operations --------------------------------------------------
    def send_sms(self, number, text):
        with self.lock:
            r = self._cmd(f'AT+CMGS="{number}"\r', 5)
            if ">" not in r:
                self.port[0].write(b"\x1b")  # cancel a pending prompt, just in case
                raise SmsError(f"No prompt from modem: {r or 'timeout'}")
            r = self._cmd(text + "\x1a", 30)
            m = re.search(r"\+CMGS:\s*(\d+)", r)
            if m and "OK" in r:
                return m.group(1)
            raise SmsError(r or "timeout waiting for send confirmation")

    def signal(self):
        if not self.connected or not self.lock.acquire(timeout=2):
            return None
        try:
            m = re.search(r"\+CSQ:\s*(\d+)", self._cmd("AT+CSQ\r"))
            return int(m.group(1)) if m else None
        except usb.core.USBError:
            return None
        finally:
            self.lock.release()


modem = Modem()

# ---------------------------------------------------------------- database
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            to_number TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',   -- queued | sending | sent | failed
            attempts INTEGER NOT NULL DEFAULT 0,
            ref TEXT,
            error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            sent_at TEXT)""")
        # messages interrupted by a crash go back to the queue
        c.execute("UPDATE messages SET status='queued' WHERE status='sending'")
        c.commit()


def update(msg_id, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    with closing(db()) as c:
        c.execute(f"UPDATE messages SET {cols} WHERE id=?", (*fields.values(), msg_id))
        c.commit()


# ---------------------------------------------------------------- worker
def worker():
    while True:
        if not modem.connected:
            try:
                modem.connect()
                state["last_error"] = None
            except Exception as e:
                state["last_error"] = str(e)
                log(f"Modem not available: {e}. Retrying in 10 s")
                time.sleep(10)
                continue

        with closing(db()) as c:
            row = c.execute(
                "SELECT * FROM messages WHERE status='queued' ORDER BY id LIMIT 1"
            ).fetchone()
        if row is None:
            time.sleep(1)
            continue

        update(row["id"], status="sending")
        attempts = row["attempts"] + 1
        try:
            ref = modem.send_sms(row["to_number"], row["body"])
            update(row["id"], status="sent", ref=ref, attempts=attempts, error=None,
                   sent_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            log(f"#{row['id']} sent to {row['to_number']} (ref {ref})")
        except usb.core.USBError as e:
            # dongle unplugged or USB failure: requeue without counting an attempt
            update(row["id"], status="queued", error=f"USB: {e}")
            state["last_error"] = f"USB: {e}"
            log(f"USB error, reconnecting: {e}")
            modem.disconnect()
            time.sleep(5)
            continue
        except SmsError as e:
            status = "failed" if attempts >= MAX_ATTEMPTS else "queued"
            update(row["id"], status=status, attempts=attempts, error=str(e))
            log(f"#{row['id']} error (attempt {attempts}): {e}")
        time.sleep(SEND_DELAY)


# ---------------------------------------------------------------- helpers
def normalize_number(n):
    n = re.sub(r"[\s\-.()]", "", n)
    if n.startswith("00"):
        n = "+" + n[2:]
    if re.fullmatch(r"[37]\d{8}", n):  # local Senegal number, e.g. 785360996
        n = "+221" + n
    if not re.fullmatch(r"\+\d{8,15}", n):
        raise ValueError(f"invalid number: {n}")
    return n


REPLACEMENTS = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "€": "EUR"}


def clean_text(t):
    # Text mode is safe with plain ASCII only: é -> e, à -> a (UCS2 support comes later)
    for a, b in REPLACEMENTS.items():
        t = t.replace(a, b)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    t = t.replace("\x1a", "").replace("\x1b", "").strip()
    if not t:
        raise ValueError("empty message")
    if len(t) > MAX_LEN:
        raise ValueError(f"message too long ({len(t)} > {MAX_LEN} characters)")
    return t


# ---------------------------------------------------------------- API
app = FastAPI(title="SMS Gateway", version="1.0")


def check_key(x_api_key: str = Header(default="")):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


class SmsIn(BaseModel):
    to: str
    text: str


class BulkIn(BaseModel):
    messages: list[SmsIn]


def enqueue(items):
    rows = []
    for i, m in enumerate(items):
        try:
            rows.append((normalize_number(m.to), clean_text(m.text)))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"message {i}: {e}")
    ids = []
    with closing(db()) as c:
        for number, body in rows:
            cur = c.execute("INSERT INTO messages (to_number, body) VALUES (?, ?)", (number, body))
            ids.append(cur.lastrowid)
        c.commit()
    return ids


@app.post("/sms", dependencies=[Depends(check_key)])
def send_one(msg: SmsIn):
    return {"id": enqueue([msg])[0], "status": "queued"}


@app.post("/sms/bulk", dependencies=[Depends(check_key)])
def send_bulk(payload: BulkIn):
    ids = enqueue(payload.messages)
    return {"ids": ids, "count": len(ids), "status": "queued"}


@app.get("/sms/{msg_id}", dependencies=[Depends(check_key)])
def get_one(msg_id: int):
    with closing(db()) as c:
        row = c.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return dict(row)


@app.get("/sms", dependencies=[Depends(check_key)])
def list_messages(status: str | None = None, limit: int = 50):
    q, args = "SELECT * FROM messages", []
    if status:
        q, args = q + " WHERE status=?", [status]
    with closing(db()) as c:
        rows = c.execute(q + " ORDER BY id DESC LIMIT ?", (*args, min(limit, 500))).fetchall()
    return [dict(r) for r in rows]


@app.get("/status", dependencies=[Depends(check_key)])
def status():
    with closing(db()) as c:
        counts = dict(c.execute("SELECT status, COUNT(*) FROM messages GROUP BY status").fetchall())
    return {
        "modem_connected": modem.connected,
        "signal_csq": modem.signal(),
        "queue": counts,
        "last_error": state["last_error"],
        "started_at": state["started_at"],
    }


if __name__ == "__main__":
    init_db()
    threading.Thread(target=worker, daemon=True).start()
    log(f"SMS Gateway listening on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
