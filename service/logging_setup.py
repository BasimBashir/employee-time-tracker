"""Structured JSON-lines logging to <data_root>\\logs\\service.log.

The UI's log viewer reads these files, so every line must be a single JSON object."""
import json
import logging
import logging.handlers
from pathlib import Path

DEFAULT_LOG_DIR = Path(r"C:\ProgramData\EmployeeTracker\logs")
ROTATE_BYTES = 20 * 1024 * 1024
BACKUP_COUNT = 5


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "thread": record.threadName,
        })


def service_logger(name: str, log_dir: Path | None = None) -> logging.Logger:
    """Named logger writing JSON-lines to `log_dir/service.log` (rotating).

    Calling it twice with the same name returns the same logger without adding
    duplicate handlers — the service and its threads share one logger per name.
    """
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    log.propagate = False
    if log.handlers:
        return log
    if log_dir is None:
        log_dir = DEFAULT_LOG_DIR
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "service.log", maxBytes=ROTATE_BYTES, backupCount=BACKUP_COUNT,
        encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    log.addHandler(file_handler)

    stream = logging.StreamHandler()
    stream.setFormatter(JsonFormatter())
    log.addHandler(stream)
    return log
