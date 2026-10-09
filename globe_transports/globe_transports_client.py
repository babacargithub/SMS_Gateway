"""HTTP client for the Globe Transports backend (departs and the contacts of their bookings). No UI.

Endpoints (under /api):
  POST /api/sms_gateway/token  {"username", "password"} -> {"token", "expires_at"}  (the token lives 90 days; no authentication needed)
  GET /api/departs_for_sms     ?period=upcoming|past[&limit=N]                         the departs and their buses
  GET /api/contacts_for_sms    ?entity=bus|depart&entity_ids[]=1&entity_ids[]=2&filter=paid|unpaid|all   one object per booking
The two GET routes need `Authorization: Bearer <token>`.

The token is kept by a token store (the Keychain in the app); the password is never kept. A token with less than 3 days left,
or one the server refuses (401), is dropped and `login_requester` is asked to log in again (in the app: the login dialog), then
the request that failed is retried ONCE. Neither the token nor the password is ever put in a message or a log.

The HTTP layer is a plain function (`urllib_transport`) so tests can replace it and never touch the network.
Every failure becomes a `GlobeTransportsError` whose message is in French and can be shown as it is."""
import json
import socket
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
HTTP_TIMEOUT_SECONDS = 10
TOKEN_ROUTE = "sms_gateway/token"
TOKEN_MINIMUM_REMAINING_LIFETIME = timedelta(days=3)   # below this the token is replaced before it can fail in the middle of an import
UPCOMING_PERIOD = "upcoming"
PAST_PERIOD = "past"
ENTITY_DEPART = "depart"
ENTITY_BUS = "bus"
FILTER_PAID = "paid"
FILTER_UNPAID = "unpaid"
FILTER_ALL = "all"


class GlobeTransportsError(Exception):
    """A French message for the owner: the server is unreachable, slow, refused the request or answered nonsense."""


class GlobeAuthenticationRequired(GlobeTransportsError):
    """No usable token and no way (or no wish) to log in: the owner has to log in to Globe Transports."""


class GlobeConnectionFailed(Exception):
    """Raised by a transport when the server cannot be reached at all."""


class GlobeRequestTimedOut(Exception):
    """Raised by a transport when the server did not answer within the timeout."""


@dataclass
class GlobeToken:
    token: str
    expires_at: datetime   # timezone-aware
    base_url: str          # the server it was issued by: a token is useless for another server

    def is_usable(self, now, base_url):
        return self.base_url == base_url and self.expires_at - now >= TOKEN_MINIMUM_REMAINING_LIFETIME


class InMemoryTokenStore:
    """Keeps the token only while the program runs (used when no durable store is given, and in tests)."""

    def __init__(self, token=None):
        self.stored_token = token

    def load(self):
        return self.stored_token

    def save(self, token):
        self.stored_token = token

    def clear(self):
        self.stored_token = None


def parse_expiry(expires_at_text):
    expires_at = datetime.fromisoformat(expires_at_text.strip().replace("Z", "+00:00"))
    return expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)


@dataclass
class BusForSms:
    bus_id: int
    name: str


@dataclass
class DepartForSms:
    depart_id: int
    name: str
    buses: list = field(default_factory=list)   # [BusForSms]


def normalize_base_url(typed_base_url):
    """'localhost:8000/' -> 'http://localhost:8000'; a trailing '/api' is dropped because the client adds it. Empty: the default."""
    base_url = typed_base_url.strip().rstrip("/")
    if not base_url:
        return DEFAULT_BASE_URL
    if "://" not in base_url:
        base_url = "http://" + base_url
    if base_url.endswith("/api"):
        base_url = base_url[: -len("/api")]
    return base_url


def urllib_transport(http_method, url, headers, body_bytes, timeout_seconds):
    """Returns (http_status, response_text). Raises GlobeConnectionFailed / GlobeRequestTimedOut."""
    request = urllib.request.Request(url, data=body_bytes, headers=headers, method=http_method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as http_error:
        return http_error.code, http_error.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as url_error:
        if isinstance(url_error.reason, (socket.timeout, TimeoutError)):
            raise GlobeRequestTimedOut(str(url_error.reason)) from url_error
        raise GlobeConnectionFailed(str(url_error.reason)) from url_error
    except (socket.timeout, TimeoutError) as timeout_error:
        raise GlobeRequestTimedOut(str(timeout_error)) from timeout_error
    except OSError as connection_error:
        raise GlobeConnectionFailed(str(connection_error)) from connection_error


def describe_refusal(response_text):
    """The backend's own explanation when it sends one (Laravel validation: {"message": "..."}), else ''."""
    try:
        parsed_body = json.loads(response_text)
    except ValueError:
        return ""
    if isinstance(parsed_body, dict) and isinstance(parsed_body.get("message"), str):
        return parsed_body["message"].strip()
    return ""


def utc_now():
    return datetime.now(timezone.utc)


class GlobeTransportsClient:
    def __init__(self, base_url=DEFAULT_BASE_URL, transport=urllib_transport, token_store=None, login_requester=None, clock=utc_now):
        """`login_requester()`: asks the owner to log in (it calls `login` itself) and returns True when a token is now stored."""
        self.base_url = normalize_base_url(base_url)
        self.transport = transport
        self.token_store = token_store or InMemoryTokenStore()
        self.login_requester = login_requester
        self.clock = clock

    # ---- raw HTTP -------------------------------------------------------------------------------

    def send_request(self, http_method, route, query_pairs=(), json_body=None, bearer_token=None):
        """Returns (http_status, response_text); a server that cannot be reached or does not answer becomes a French error."""
        url = f"{self.base_url}/api/{route}" + (f"?{urllib.parse.urlencode(list(query_pairs))}" if query_pairs else "")
        headers = {"Accept": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        body_bytes = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            body_bytes = json.dumps(json_body).encode("utf-8")
        try:
            return self.transport(http_method, url, headers, body_bytes, HTTP_TIMEOUT_SECONDS)
        except GlobeRequestTimedOut as timed_out:
            raise GlobeTransportsError(
                f"Globe Transports ne répond pas (délai de {HTTP_TIMEOUT_SECONDS} s dépassé). Réessayez dans un instant."
            ) from timed_out
        except GlobeConnectionFailed as connection_failed:
            raise GlobeTransportsError(
                f"Impossible de joindre Globe Transports à l'adresse {self.base_url}. Vérifiez que le serveur est démarré et que l'adresse est correcte."
            ) from connection_failed

    @staticmethod
    def parse_json(response_text):
        try:
            return json.loads(response_text)
        except ValueError as bad_json:
            raise GlobeTransportsError("Réponse illisible de Globe Transports (ce n'est pas du JSON valide).") from bad_json

    # ---- logging in -----------------------------------------------------------------------------

    def login(self, username, password):
        """Asks Globe Transports for a token and stores it (the password is used for this one request only).
        Raises GlobeTransportsError with the reason in French (wrong password, no permission, too many attempts…)."""
        http_status, response_text = self.send_request(
            "POST", TOKEN_ROUTE, json_body={"username": username.strip(), "password": password}
        )
        if http_status == 401:
            raise GlobeTransportsError(describe_refusal(response_text) or "Identifiants invalides.")
        if http_status == 403:
            raise GlobeTransportsError(describe_refusal(response_text) or "Cet utilisateur n'a pas le droit d'envoyer des messages.")
        if http_status == 422:
            raise GlobeTransportsError("Renseignez le nom d'utilisateur et le mot de passe.")
        if http_status == 429:
            raise GlobeTransportsError("Trop de tentatives, réessayez dans une minute")
        if not 200 <= http_status < 300:
            raise GlobeTransportsError(f"Connexion refusée par Globe Transports (erreur {http_status}).")
        answer = self.parse_json(response_text)
        try:
            token = GlobeToken(str(answer["token"]), parse_expiry(str(answer["expires_at"])), self.base_url)
        except (KeyError, TypeError, ValueError) as bad_shape:
            raise GlobeTransportsError("Réponse inattendue de Globe Transports pendant la connexion.") from bad_shape
        if not token.token:
            raise GlobeTransportsError("Réponse inattendue de Globe Transports pendant la connexion.")
        try:
            self.token_store.save(token)
        except RuntimeError as keychain_problem:   # the Keychain's message is already French and holds no secret
            raise GlobeTransportsError(str(keychain_problem)) from keychain_problem

    def logout(self):
        self.token_store.clear()

    def usable_stored_token(self):
        """The stored token, or None. One that is missing, for another server, or has under 3 days left is dropped."""
        stored_token = self.token_store.load()
        if stored_token is None:
            return None
        if stored_token.is_usable(self.clock(), self.base_url):
            return stored_token
        self.token_store.clear()
        return None

    def obtain_token(self):
        usable_token = self.usable_stored_token()
        if usable_token is not None:
            return usable_token
        if self.login_requester is not None and self.login_requester():
            usable_token = self.usable_stored_token()
            if usable_token is not None:
                return usable_token
        raise GlobeAuthenticationRequired("Connexion à Globe Transports requise : connectez-vous avec votre nom d'utilisateur et votre mot de passe.")

    # ---- authenticated reads --------------------------------------------------------------------

    def get_json(self, route, query_pairs):
        """GET with the token. A 401 drops the token, logs in again and retries once."""
        for attempt_number in (1, 2):
            token = self.obtain_token()
            http_status, response_text = self.send_request("GET", route, query_pairs, bearer_token=token.token)
            if http_status == 401:
                self.token_store.clear()
                if attempt_number == 2:
                    raise GlobeAuthenticationRequired("Globe Transports a refusé votre connexion. Connectez-vous de nouveau.")
                continue
            if http_status == 403:
                raise GlobeTransportsError(
                    describe_refusal(response_text) or "Votre compte n'a plus la permission d'envoyer des messages (Globe Transports)."
                )
            if not 200 <= http_status < 300:
                explanation = describe_refusal(response_text)
                raise GlobeTransportsError(
                    f"Globe Transports a refusé la demande (erreur {http_status})" + (f" : {explanation}" if explanation else ".")
                )
            return self.parse_json(response_text)

    def fetch_departs(self, period, limit=None):
        """The departs with their buses, in the order the backend gives (upcoming: soonest first, past: latest first)."""
        query_pairs = [("period", period)]
        if limit is not None:
            query_pairs.append(("limit", limit))
        response_data = self.get_json("departs_for_sms", query_pairs)
        if not isinstance(response_data, list):
            raise GlobeTransportsError("Réponse inattendue de Globe Transports : une liste de départs était attendue.")
        departs = []
        try:
            for depart_data in response_data:
                buses = [BusForSms(int(bus_data["id"]), str(bus_data.get("name") or "")) for bus_data in depart_data.get("buses") or []]
                departs.append(DepartForSms(int(depart_data["id"]), str(depart_data.get("depart_name") or ""), buses))
        except (KeyError, TypeError, ValueError, AttributeError) as bad_shape:
            raise GlobeTransportsError("Réponse inattendue de Globe Transports : la liste des départs est mal formée.") from bad_shape
        return departs

    def fetch_bookings(self, entity, entity_ids, payment_filter=FILTER_ALL):
        """One call: the bookings (dicts) of the given buses or departs."""
        query_pairs = [("entity", entity)] + [("entity_ids[]", entity_id) for entity_id in entity_ids] + [("filter", payment_filter)]
        response_data = self.get_json("contacts_for_sms", query_pairs)
        if not isinstance(response_data, list) or not all(isinstance(booking, dict) for booking in response_data):
            raise GlobeTransportsError("Réponse inattendue de Globe Transports : une liste de réservations était attendue.")
        return response_data

    def fetch_bookings_for_selection(self, depart_ids, bus_ids, payment_filter=FILTER_ALL):
        """Whole departs and individual buses may both be chosen: one call each, merged without the same booking twice."""
        bookings = []
        if depart_ids:
            bookings += self.fetch_bookings(ENTITY_DEPART, depart_ids, payment_filter)
        if bus_ids:
            bookings += self.fetch_bookings(ENTITY_BUS, bus_ids, payment_filter)
        booking_ids_already_kept = set()
        merged_bookings = []
        for booking in bookings:
            booking_id = booking.get("booking_id")
            if booking_id is not None:
                if booking_id in booking_ids_already_kept:
                    continue
                booking_ids_already_kept.add(booking_id)
            merged_bookings.append(booking)
        return merged_bookings
