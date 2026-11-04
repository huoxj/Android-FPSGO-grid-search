"""Console logging: one line per event, long waits drawn in place.

The tool sits in front of an operator for hours, so the output has to stay
readable: real events go through logging (one line each) and anything that
repeats every few seconds paints a status line the next record overwrites.
Without wiping it first, a log line lands in the middle of the status text and
both become unreadable.

When stdout is not a terminal (a redirected log) the status line is dropped
instead of truncated to 80 columns; the periodic INFO lines are the trail then.
"""
import logging
import shutil
import sys
import atexit

_LEVEL = {"DEBUG": "D", "INFO": "I", "WARNING": "W", "ERROR": "E", "CRITICAL": "E"}

# How often a status line is also written to the log as a normal record, so a
# saved log still shows what a long wait was doing.
STATUS_LOG_EVERY = 300

_status_len = 0
_tty = sys.stdout.isatty()


class _ConsoleFormatter(logging.Formatter):
    def format(self, record):
        return (f"{self.formatTime(record, '%H:%M:%S')} "
                f"{_LEVEL.get(record.levelname, '?')} "
                f"{record.getMessage()}")


def _clear_status() -> None:
    global _status_len
    if _status_len:
        sys.stdout.write("\r" + " " * _status_len + "\r")
        sys.stdout.flush()
        _status_len = 0


class _StatusAwareStream(logging.StreamHandler):
    """Wipe the status line before writing a record, or the two would overlap."""

    def emit(self, record):
        _clear_status()
        super().emit(record)


def status(msg: str) -> None:
    """Paint a line in place; the next status() or log record replaces it."""
    global _status_len
    if not _tty:
        return
    width = shutil.get_terminal_size((120, 24)).columns
    line = msg if len(msg) < width else msg[: width - 1]
    pad = max(0, _status_len - len(line))
    sys.stdout.write("\r" + line + " " * pad)
    sys.stdout.flush()
    _status_len = len(line)


def stage(name: str) -> None:
    """Mark a phase boundary, so the log reads as a sequence of phases."""
    logging.getLogger("grid").info("-- %s --", name)


def setup(level: str = "INFO", log_file: str | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    console = _StatusAwareStream(sys.stdout)
    console.setFormatter(_ConsoleFormatter())
    root.addHandler(console)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root.addHandler(fh)
    # A status line still on screen at exit would swallow the shell prompt.
    atexit.register(_clear_status)
