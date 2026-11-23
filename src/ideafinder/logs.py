"""What the terminal shows while the app runs: one readable line per thing that happened.

Library chatter (a line per HTTP request, per queued job, per page load, two heartbeats a
minute) is hidden unless it's a warning or an error. Set IDEAFINDER_VERBOSE=1 to see it all.
"""

import logging
import os

# Loggers that report routine work at INFO. Their warnings and errors still show.
CHATTY = ("httpx", "httpcore", "procrastinate", "uvicorn.access", "alembic")


class _Formatter(logging.Formatter):
    """`20:41:07  message` for progress; level and source added for warnings and errors."""

    def format(self, record: logging.LogRecord) -> str:
        when = self.formatTime(record, "%H:%M:%S")
        text = record.getMessage()
        if record.levelno >= logging.WARNING:
            text = f"{record.levelname}  {record.name}: {text}"
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return f"{when}  {text}"


def configure() -> None:
    verbose = os.environ.get("IDEAFINDER_VERBOSE", "") not in ("", "0")
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        if verbose
        else _Formatter()
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for name in CHATTY:
        logging.getLogger(name).setLevel(logging.INFO if verbose else logging.WARNING)


def duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, secs = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"
