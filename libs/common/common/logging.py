import os
import sys
from typing import Any

LOG_LEVEL = "INFO"
DEFAULT_LOG_DIR = "/log"
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 3


def _can_write_log_file(log_dir: str, log_file: str) -> bool:
    # Checked up front because a RotatingFileHandler that cannot open its file
    # makes dictConfig raise, which would crash the app at import time.
    if not (os.path.isdir(log_dir) and os.access(log_dir, os.W_OK | os.X_OK)):
        return False
    return not os.path.exists(log_file) or os.access(log_file, os.W_OK)


def logging_config(app_logger_name: str, log_dir: str | None = None) -> dict[str, Any]:
    """Build a dictConfig for the app logger: console plus a rotating file.

    The file is ``<log_dir>/<app_logger_name>.log``; ``log_dir`` defaults to
    ``/log`` (each container's bind-mounted log directory). If the directory is
    missing or unwritable, logging falls back to console only with a warning on
    stderr.
    """
    if log_dir is None:
        log_dir = DEFAULT_LOG_DIR
    handlers: dict[str, dict[str, Any]] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "std_out",
            "stream": "ext://sys.stdout",
            "level": LOG_LEVEL,
        },
    }
    logger_handlers = ["console"]

    log_file = os.path.join(log_dir, f"{app_logger_name}.log")
    if _can_write_log_file(log_dir, log_file):
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "std_out",
            "filename": log_file,
            "maxBytes": LOG_FILE_MAX_BYTES,
            "backupCount": LOG_FILE_BACKUP_COUNT,
            "encoding": "utf-8",
            "level": LOG_LEVEL,
        }
        logger_handlers.append("file")
    else:
        print(
            f"WARNING: log file {log_file} is unavailable (missing or unwritable "
            "directory or file); logging to console only",
            file=sys.stderr,
        )

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": handlers,
        "formatters": {
            "std_out": {
                "format": "%(asctime)s %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "loggers": {
            app_logger_name: {
                "handlers": logger_handlers,
                "level": LOG_LEVEL,
                "propagate": False,
            }
        },
    }
