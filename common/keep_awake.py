"""Keep the computer awake while the app is busy (no extra process).

macOS: a "PreventUserIdleSystemSleep" IOKit assertion owned by this process stops idle system sleep and also exempts the
app from App Nap, so a locked screen or a hidden window can no longer slow down or pause a sending run.
Windows: `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` on the calling thread does the same for idle sleep.
On both, the screen may still turn off and lock. It cannot stop a closed lid or a manual Sleep."""
import ctypes
import logging
from contextlib import contextmanager

from common.platform_support import IS_WINDOWS

PREVENT_IDLE_SYSTEM_SLEEP_ASSERTION_TYPE = "PreventUserIdleSystemSleep"
ASSERTION_LEVEL_ON = 255
CF_STRING_ENCODING_UTF8 = 0x08000100

WINDOWS_ES_CONTINUOUS = 0x80000000
WINDOWS_ES_SYSTEM_REQUIRED = 0x00000001


def _load_frameworks():
    iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    core_foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    core_foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
    core_foundation.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
    iokit.IOPMAssertionCreateWithName.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
    ]
    iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int32
    iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
    iokit.IOPMAssertionRelease.restype = ctypes.c_int32
    return iokit, core_foundation


@contextmanager
def _keep_windows_awake():
    """The execution state belongs to the calling thread, so the `with` block must start and end on the same thread
    (the sending worker does)."""
    set_thread_execution_state = None
    try:
        set_thread_execution_state = ctypes.windll.kernel32.SetThreadExecutionState
        set_thread_execution_state.restype = ctypes.c_uint32
        set_thread_execution_state.argtypes = [ctypes.c_uint32]
        if set_thread_execution_state(WINDOWS_ES_CONTINUOUS | WINDOWS_ES_SYSTEM_REQUIRED) == 0:
            logging.warning("Windows refused SetThreadExecutionState: the computer may sleep during the run")
            set_thread_execution_state = None
    except (OSError, AttributeError):
        logging.exception("Keep-awake unavailable on Windows")
        set_thread_execution_state = None
    try:
        yield
    finally:
        if set_thread_execution_state is not None:
            set_thread_execution_state(WINDOWS_ES_CONTINUOUS)


@contextmanager
def _keep_mac_awake(reason_shown_to_user):
    assertion_id = ctypes.c_uint32(0)
    iokit = None
    try:
        iokit, core_foundation = _load_frameworks()
        assertion_type = core_foundation.CFStringCreateWithCString(None, PREVENT_IDLE_SYSTEM_SLEEP_ASSERTION_TYPE.encode(), CF_STRING_ENCODING_UTF8)
        assertion_name = core_foundation.CFStringCreateWithCString(None, reason_shown_to_user.encode(), CF_STRING_ENCODING_UTF8)
        creation_status = iokit.IOPMAssertionCreateWithName(assertion_type, ASSERTION_LEVEL_ON, assertion_name, ctypes.byref(assertion_id))
        core_foundation.CFRelease(assertion_type)
        core_foundation.CFRelease(assertion_name)
        if creation_status != 0:
            logging.warning("Could not hold the keep-awake assertion (IOReturn %s)", creation_status)
            assertion_id = ctypes.c_uint32(0)
    except (OSError, AttributeError):
        logging.exception("Keep-awake assertion unavailable")
        assertion_id = ctypes.c_uint32(0)
    try:
        yield
    finally:
        if iokit is not None and assertion_id.value:
            iokit.IOPMAssertionRelease(assertion_id.value)


def keep_computer_awake(reason_shown_to_user):
    """Context manager: no idle sleep inside the `with` block. If the system refuses it logs and carries on:
    sending must never fail because of this."""
    if IS_WINDOWS:
        return _keep_windows_awake()
    return _keep_mac_awake(reason_shown_to_user)
