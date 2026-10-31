"""Small secrets (API credentials, tokens) kept by the operating system, never written to disk in clear text.

macOS: the Keychain, through the `security` command line tool (generic-password items, service « SMS Gateway »).
Windows: the Credential Manager (Gestionnaire d'identification), generic credentials named « SMS Gateway/<account> »,
through the Win32 Cred* functions (ctypes, no extra dependency).

Every function takes an account name and a text value (the callers store small JSON blobs). Reading never raises:
None means "nothing saved or unreadable". Writing raises RuntimeError with a French message."""
import ctypes
import subprocess

from common.platform_support import IS_WINDOWS

SECRET_SERVICE_NAME = "SMS Gateway"
KEYCHAIN_COMMAND_TIMEOUT_SECONDS = 10

SECRET_STORE_NAME_FOR_USER = "Gestionnaire d'identification Windows" if IS_WINDOWS else "Trousseau d'accès"

WINDOWS_CRED_TYPE_GENERIC = 1
WINDOWS_CRED_PERSIST_LOCAL_MACHINE = 2  # kept across reboots, for this Windows user on this PC only
WINDOWS_MAX_SECRET_BYTES = 5 * 512


if IS_WINDOWS:
    from ctypes import wintypes

    class _WindowsCredential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_WindowsCredential))]
    _advapi32.CredReadW.restype = wintypes.BOOL
    _advapi32.CredWriteW.argtypes = [ctypes.POINTER(_WindowsCredential), wintypes.DWORD]
    _advapi32.CredWriteW.restype = wintypes.BOOL
    _advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _advapi32.CredDeleteW.restype = wintypes.BOOL
    _advapi32.CredFree.argtypes = [ctypes.c_void_p]
    _advapi32.CredFree.restype = None


def _windows_target_name(account_name):
    return f"{SECRET_SERVICE_NAME}/{account_name}"


def _read_windows_secret(account_name):
    credential_pointer = ctypes.POINTER(_WindowsCredential)()
    if not _advapi32.CredReadW(_windows_target_name(account_name), WINDOWS_CRED_TYPE_GENERIC, 0, ctypes.byref(credential_pointer)):
        return None
    try:
        credential = credential_pointer.contents
        secret_bytes = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return secret_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None
    finally:
        _advapi32.CredFree(credential_pointer)


def _write_windows_secret(account_name, secret_text):
    secret_bytes = secret_text.encode("utf-8")
    if len(secret_bytes) > WINDOWS_MAX_SECRET_BYTES:
        raise RuntimeError(f"{SECRET_STORE_NAME_FOR_USER} : donnée trop longue pour être enregistrée.")
    secret_buffer = (ctypes.c_ubyte * len(secret_bytes)).from_buffer_copy(secret_bytes)
    credential = _WindowsCredential()
    credential.Type = WINDOWS_CRED_TYPE_GENERIC
    credential.TargetName = _windows_target_name(account_name)
    credential.CredentialBlobSize = len(secret_bytes)
    credential.CredentialBlob = ctypes.cast(secret_buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = WINDOWS_CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = account_name
    if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise RuntimeError(f"Le {SECRET_STORE_NAME_FOR_USER} a refusé l'enregistrement (erreur {ctypes.get_last_error()}).")


def _run_keychain_command(arguments):
    return subprocess.run(["security", *arguments], capture_output=True, text=True, timeout=KEYCHAIN_COMMAND_TIMEOUT_SECONDS)


def read_secret(account_name):
    """The saved text, or None when nothing was saved (or the store cannot be read)."""
    if IS_WINDOWS:
        try:
            return _read_windows_secret(account_name)
        except OSError:
            return None
    try:
        completed_command = _run_keychain_command(["find-generic-password", "-s", SECRET_SERVICE_NAME, "-a", account_name, "-w"])
    except (subprocess.SubprocessError, OSError):
        return None
    if completed_command.returncode != 0:
        return None
    return completed_command.stdout.strip()


def write_secret(account_name, secret_text):
    """Create or replace the secret. Raises RuntimeError with a French message if the store refuses."""
    if IS_WINDOWS:
        try:
            _write_windows_secret(account_name, secret_text)
        except OSError as store_error:
            raise RuntimeError(f"{SECRET_STORE_NAME_FOR_USER} injoignable : {store_error}") from store_error
        return
    try:
        completed_command = _run_keychain_command(
            ["add-generic-password", "-U", "-s", SECRET_SERVICE_NAME, "-a", account_name, "-w", secret_text]
        )
    except (subprocess.SubprocessError, OSError) as keychain_error:
        raise RuntimeError(f"{SECRET_STORE_NAME_FOR_USER} injoignable : {keychain_error}") from keychain_error
    if completed_command.returncode != 0:
        raise RuntimeError(f"Le {SECRET_STORE_NAME_FOR_USER} a refusé l'enregistrement : {completed_command.stderr.strip()}")


def delete_secret(account_name):
    """Remove the secret if it exists. Never raises: an unreachable store is simply left as it is."""
    if IS_WINDOWS:
        try:
            _advapi32.CredDeleteW(_windows_target_name(account_name), WINDOWS_CRED_TYPE_GENERIC, 0)
        except OSError:
            pass
        return
    try:
        _run_keychain_command(["delete-generic-password", "-s", SECRET_SERVICE_NAME, "-a", account_name])
    except (subprocess.SubprocessError, OSError):
        pass
