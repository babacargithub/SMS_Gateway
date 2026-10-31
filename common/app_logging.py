"""Log file of the app: <data folder>/logs/sms_gateway.log, rotated (5 files of 1 MB), plus the console when there is one.

The packaged Windows app has no console at all, so this file is the only trace when something goes wrong at a user's
PC (the Devices page can show where it is). Uncaught exceptions, also in threads, are written there too.
Never log secrets: tokens and passwords must not reach this file."""
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from common.app_paths import APPLICATION_SUPPORT_DIRECTORY

LOG_DIRECTORY = APPLICATION_SUPPORT_DIRECTORY / "logs"
LOG_FILE_PATH = LOG_DIRECTORY / "sms_gateway.log"
LOG_FILE_MAX_BYTES = 1_000_000
LOG_FILE_BACKUP_COUNT = 4
LOG_LINE_FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"


def configure_application_logging():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter(LOG_LINE_FORMAT)
    try:
        LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE_PATH, maxBytes=LOG_FILE_MAX_BYTES, backupCount=LOG_FILE_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except OSError:
        pass  # a read-only or missing data folder must never stop the app
    if sys.stderr is not None:  # None in the packaged (windowed) app
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    def log_uncaught_exception(exception_type, exception_value, exception_traceback):
        logging.critical("Uncaught exception", exc_info=(exception_type, exception_value, exception_traceback))

    def log_uncaught_thread_exception(hook_arguments):
        logging.critical(
            "Uncaught exception in thread %s", getattr(hook_arguments.thread, "name", "?"),
            exc_info=(hook_arguments.exc_type, hook_arguments.exc_value, hook_arguments.exc_traceback),
        )

    sys.excepthook = log_uncaught_exception
    threading.excepthook = log_uncaught_thread_exception
