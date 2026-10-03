"""Keep the Mac awake while the app is busy (macOS power assertion through IOKit, no extra process).

A "PreventUserIdleSystemSleep" assertion owned by this process stops idle system sleep and also exempts the app from
App Nap, so a locked screen or a hidden window can no longer slow down or pause a sending run. The screen may still
turn off and lock. It cannot stop a closed lid or a manual Sleep."""
import ctypes
import logging
from contextlib import contextmanager

PREVENT_IDLE_SYSTEM_SLEEP_ASSERTION_TYPE = "PreventUserIdleSystemSleep"
ASSERTION_LEVEL_ON = 255
CF_STRING_ENCODING_UTF8 = 0x08000100


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
def keep_mac_awake(reason_shown_to_user):
    """Hold the assertion inside the `with` block. If macOS refuses (or this is not macOS) it logs and carries on:
    sending must never fail because of this."""
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
