"""Keeping a session's token out of the server's own log lines.

The clients open the socket bare and send the token as the first frame, and the phone's page
carries it in the address's fragment; the gateway's own lines name a path without its query.
What remained was uvicorn: its request line is the path with the query string as it arrived,
so a page or an app still on the older ``?token=`` form put the token in the log with every
upgrade (``"WebSocket /ws?token=…"``, on ``uvicorn.error``) and every page load (``"GET
/?token=…"``, on ``uvicorn.access``); and httpx, which names the whole URL of every request
the gateway relays to a container at INFO. The filter below sits on those loggers and rewrites
any ``token=`` value before a handler sees the record. The older form is still accepted this
release; it is only never written down.
"""

from __future__ import annotations

import logging
import re

# a token value: everything up to the next query separator, whitespace or quote
_TOKEN = re.compile(r"(?i)(\btoken=)[^&\s\"'#]+")
REDACTED = "[redacted]"

# where a request line with its query is written: uvicorn's access log for HTTP, its error
# logger for the WebSocket handshake ("WebSocket /ws" [accepted] / 403 / <code>), and httpx
# (with httpcore underneath) for what the gateway itself asks of a container; httpx 2 ships as
# the httpx2 package and logs under that name (Starlette's test client prefers it when it is
# installed), so it is covered too
REQUEST_LOGGERS = ("uvicorn.access", "uvicorn.error", "httpx", "httpx2", "httpcore")


def redact(text: str) -> str:
    """``…?token=secret&ui=lite`` → ``…?token=[redacted]&ui=lite``."""
    return _TOKEN.sub(rf"\g<1>{REDACTED}", text)


def _clean(value: object) -> object:
    """The argument with its token redacted: a string rewritten, an object (httpx passes its
    ``URL``) replaced by its redacted text when the text carries one, anything else as is."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    text = str(value)
    cleaned = redact(text)
    return cleaned if cleaned != text else value


class RedactTokens(logging.Filter):
    """Rewrites ``token=`` values in a record's message and its arguments. Applied on the
    logger, so every handler, whatever its format, gets the redacted record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_clean(a) for a in args)
        elif isinstance(args, dict):
            record.args = {k: _clean(v) for k, v in args.items()}
        return True


def redact_tokens_in_logs(names: tuple[str, ...] = REQUEST_LOGGERS) -> None:
    """Put the filter on the request loggers (once; calling again is harmless). Done when the
    app is built, which is after uvicorn has configured its logging, so the filter stays."""
    for name in names:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactTokens) for f in logger.filters):
            logger.addFilter(RedactTokens())
