"""HTTP client for the Orange SMS API (Sénégal): OAuth token, send one SMS, read the remaining balance. No UI.

Endpoints (same ones the GoTransV5 project uses):
  POST https://api.orange.com/oauth/v3/token                              client credentials, Basic auth
  POST https://api.orange.com/smsmessaging/v1/outbound/<tel:sender>/requests   send one SMS, Bearer token
  GET  https://api.orange.com/sms/admin/v1/contracts                      remaining SMS units per contract

The HTTP layer is a plain function (`urllib_transport`) so tests can replace it and never touch the network."""
import base64
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

ORANGE_API_BASE_URL = "https://api.orange.com"
TOKEN_URL = f"{ORANGE_API_BASE_URL}/oauth/v3/token"
CONTRACTS_URL = f"{ORANGE_API_BASE_URL}/sms/admin/v1/contracts"
HTTP_TIMEOUT_SECONDS = 20
TOKEN_SAFETY_MARGIN_SECONDS = 60  # renew the token a little before it really expires
ACTIVE_CONTRACT_STATUS = "ACTIVE"


class OrangeConnectionFailed(Exception):
    """The request certainly never reached Orange (no network, DNS failure, connection refused): nothing was sent."""


class OrangeResponseTimedOut(Exception):
    """The request may or may not have been processed by Orange: the outcome is unknown."""


class OrangeHttpError(Exception):
    def __init__(self, http_status, detail):
        super().__init__(f"HTTP {http_status} : {detail}")
        self.http_status = http_status
        self.detail = detail


@dataclass
class OrangeSmsBalance:
    available_units: int
    nearest_expiration: datetime | None  # None when there is no active contract


def urllib_transport(http_method, url, headers, body_bytes, timeout_seconds):
    """Returns (http_status, response_text). Raises OrangeConnectionFailed / OrangeResponseTimedOut."""
    request = urllib.request.Request(url, data=body_bytes, headers=headers, method=http_method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as http_error:
        return http_error.code, http_error.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as url_error:
        if isinstance(url_error.reason, (socket.gaierror, ConnectionRefusedError, ConnectionResetError, OSError)) \
                and not isinstance(url_error.reason, socket.timeout):
            raise OrangeConnectionFailed(str(url_error.reason)) from url_error
        raise OrangeResponseTimedOut(str(url_error.reason)) from url_error
    except (socket.timeout, TimeoutError) as timeout_error:
        raise OrangeResponseTimedOut(str(timeout_error)) from timeout_error


def describe_error_body(response_text):
    """Orange reports errors in a few JSON shapes (requestError.policyException / serviceException, or code+message+description)."""
    try:
        parsed_body = json.loads(response_text)
    except ValueError:
        return response_text.strip()[:200] or "réponse vide"
    if isinstance(parsed_body, dict):
        request_error = parsed_body.get("requestError")
        if isinstance(request_error, dict):
            for exception_kind in ("policyException", "serviceException"):
                exception_details = request_error.get(exception_kind)
                if isinstance(exception_details, dict):
                    return f"{exception_details.get('messageId', '')} {exception_details.get('text', '')}".strip()
        readable_parts = [str(parsed_body[key]) for key in ("code", "message", "description", "error_description") if key in parsed_body]
        if readable_parts:
            return " — ".join(readable_parts)
    return response_text.strip()[:200]


class OrangeSmsClient:
    def __init__(self, credentials, transport=urllib_transport, clock=time.monotonic):
        self.credentials = credentials
        self.transport = transport
        self.clock = clock
        self.cached_access_token = None
        self.cached_token_expires_at = 0.0
        self.token_lock = threading.Lock()  # the sending thread and the balance thread share one client

    def get_access_token(self):
        with self.token_lock:
            if self.cached_access_token and self.clock() < self.cached_token_expires_at:
                return self.cached_access_token
            basic_credentials = base64.b64encode(
                f"{self.credentials.client_id}:{self.credentials.client_secret}".encode()
            ).decode()
            http_status, response_text = self.transport(
                "POST", TOKEN_URL,
                {"Authorization": f"Basic {basic_credentials}", "Content-Type": "application/x-www-form-urlencoded"},
                b"grant_type=client_credentials", HTTP_TIMEOUT_SECONDS,
            )
            if http_status != 200:
                raise OrangeHttpError(http_status, describe_error_body(response_text))
            token_response = json.loads(response_text)
            self.cached_access_token = token_response["access_token"]
            self.cached_token_expires_at = self.clock() + int(token_response.get("expires_in", 3600)) - TOKEN_SAFETY_MARGIN_SECONDS
            return self.cached_access_token

    def forget_access_token(self):
        with self.token_lock:
            self.cached_access_token = None

    def send_text_message(self, recipient_number, message_text):
        """Returns True when Orange accepted the SMS (its reply holds `outboundSMSMessageRequest`), False for an
        unexpected 2xx body. Raises OrangeHttpError / OrangeConnectionFailed / OrangeResponseTimedOut.
        A 401 (token refused) is retried once with a fresh token: Orange processed nothing, so no risk of a duplicate."""
        for attempt_number in (1, 2):
            access_token = self.get_access_token()
            http_status, response_text = self.transport(
                "POST", self._send_url(), {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json.dumps(self._send_body(recipient_number, message_text)).encode(), HTTP_TIMEOUT_SECONDS,
            )
            if http_status == 401 and attempt_number == 1:
                self.forget_access_token()
                continue
            break
        if not 200 <= http_status < 300:
            raise OrangeHttpError(http_status, describe_error_body(response_text))
        try:
            return "outboundSMSMessageRequest" in json.loads(response_text)
        except ValueError:
            return False

    def _send_url(self):
        sender_address = urllib.parse.quote(f"tel:{self.credentials.sender_number}", safe="")
        return f"{ORANGE_API_BASE_URL}/smsmessaging/v1/outbound/{sender_address}/requests"

    def _send_body(self, recipient_number, message_text):
        outbound_request = {
            "address": f"tel:{recipient_number}",
            "senderAddress": f"tel:{self.credentials.sender_number}",
            "outboundSMSTextMessage": {"message": message_text},
        }
        if self.credentials.sender_name.strip():
            outbound_request["senderName"] = self.credentials.sender_name.strip()
        return {"outboundSMSMessageRequest": outbound_request}

    def read_balance(self, now=None):
        """Remaining SMS units of every ACTIVE, not expired contract. Raises like `send_text_message`."""
        now = now or datetime.now(timezone.utc)
        for attempt_number in (1, 2):
            access_token = self.get_access_token()
            http_status, response_text = self.transport(
                "GET", CONTRACTS_URL, {"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                None, HTTP_TIMEOUT_SECONDS,
            )
            if http_status == 401 and attempt_number == 1:
                self.forget_access_token()
                continue
            break
        if http_status != 200:
            raise OrangeHttpError(http_status, describe_error_body(response_text))
        return parse_contracts_into_balance(json.loads(response_text), now)


def parse_contracts_into_balance(contracts_response, now):
    contracts = contracts_response if isinstance(contracts_response, list) else contracts_response.get("contracts", [])
    available_units = 0
    expiration_dates = []
    for contract in contracts:
        if contract.get("status") != ACTIVE_CONTRACT_STATUS:
            continue
        expiration = parse_orange_timestamp(contract.get("expirationDate"))
        if expiration is not None and expiration <= now:
            continue
        available_units += int(contract.get("availableUnits") or 0)
        if expiration is not None:
            expiration_dates.append(expiration)
    return OrangeSmsBalance(available_units, min(expiration_dates) if expiration_dates else None)


def parse_orange_timestamp(timestamp_text):
    if not timestamp_text:
        return None
    try:
        return datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
    except ValueError:
        return None
