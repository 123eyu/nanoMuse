"""Which language the gateway's own pages speak.

The phone module and the site page carry their own strings in both languages and show
the right one themselves; the few pages this process writes (a session that is over, a kept
Muse that is asleep) are chosen here: ``?lang=zh`` or ``?lang=en`` when the page asks,
else the browser's ``Accept-Language``, English otherwise.
"""

from __future__ import annotations

from starlette.requests import Request


def wants_zh(request: Request) -> bool:
    asked = request.query_params.get("lang", "").lower()
    if asked:
        return asked.startswith("zh")
    for part in request.headers.get("accept-language", "").split(","):
        tag = part.split(";")[0].strip().lower()
        if not tag:
            continue
        return tag.startswith("zh")
    return False
