"""Waking a kept Muse only for its owner.

A slept account container (accounts.py) is started again when a request on its host proves
the account's token — the way the runtime itself accepts requests: ``Authorization: Bearer``,
the socket's first ``{"kind": "auth"}`` frame, a signed inline link (``?exp=&sig=``, see
``nanomuse/server/tickets.py``; the signature is checked here with the same recipe), or the
legacy ``?token=``. Anyone can type the address; only the person has the token, so only the
person can make the box spend a container on it.

A browser arriving with nothing but the address (the page itself carries no token; a
bookmark, say) gets ``WAKE_PAGE``: a few lines that take the token from the link's fragment
or from the app's own storage on this origin and ask ``POST /__wake`` with it, then reload.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable

from starlette.requests import Request

Proof = Callable[[str], bool]

# the runtime's signed links (nanomuse/server/tickets.py): the same numbers
TICKET_MAX_AHEAD_S = 13 * 3600
TICKET_SIG_HEX = 32


def ticket_ok(
    token: str, path: str, exp: str | None, sig: str | None, now: float | None = None
) -> bool:
    if not exp or not sig or not exp.isdigit() or len(sig) != TICKET_SIG_HEX:
        return False
    now = time.time() if now is None else now
    when = int(exp)
    if when <= now or when > now + TICKET_MAX_AHEAD_S:
        return False
    mac = hmac.new(token.encode(), f"{when}\n{path}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac[:TICKET_SIG_HEX].encode(), sig.encode())


def proof_from_token(given: str | None) -> Proof | None:
    """``proves`` for a token in hand (a socket's first frame, a legacy query)."""
    if not given:
        return None
    return lambda token: secrets.compare_digest(given.encode(), token.encode())


def proof_from_http(request: Request) -> Proof:
    """``proves`` for an HTTP request: the bearer header, a signed link or ``?token=``."""
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    legacy = request.query_params.get("token", "")
    exp, sig = request.query_params.get("exp"), request.query_params.get("sig")
    path = request.url.path

    def proves(token: str) -> bool:
        if bearer and secrets.compare_digest(bearer.encode(), token.encode()):
            return True
        if legacy and secrets.compare_digest(legacy.encode(), token.encode()):
            return True
        return bool(exp and sig) and ticket_ok(token, path, exp, sig)

    return proves


def token_from_first_frame(text: str | None) -> str | None:
    """The token in the socket's first frame — ``{"kind": "auth", "token": "…"}`` — or None."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("kind") != "auth":
        return None
    token = data.get("token")
    return token if isinstance(token, str) and token else None


WAKE_PAGE = """<!doctype html><html lang="__LANG__"><meta charset="utf-8"><title>nanoMuse</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font:15px/1.5 system-ui,sans-serif;color:#1b1730;background:#f4f3fa;text-align:center;padding:24px">
<div><div style="font-size:40px">🐉</div><p id="m" style="margin:12px 0 4px;font-weight:600">__WAKING__</p>
<p id="s" style="margin:0;color:#6b6880">__MOMENT__</p></div>
<script>
(async () => {
  const m = document.getElementById("m"), s = document.getElementById("s");
  const T = __STRINGS__;
  const fromHash = (location.hash.slice(1).split("&").find((p) => p.startsWith("token=")) || "").slice(6);
  const token = fromHash || localStorage.getItem("nanomuse_token") || "";
  const signin = "__SIGNIN__";
  const again = (why) => {
    m.textContent = why;
    s.textContent = "";
    s.append(T.again_before, Object.assign(document.createElement("a"), {
      href: signin, textContent: signin.replace(/^https?:\\/\\//, "") }), T.again_after);
  };
  if (!token) return again(T.no_token);
  try {
    const r = await fetch("/__wake", { method: "POST", headers: { Authorization: "Bearer " + token } });
    if (r.ok) return location.reload();
    if (r.status === 401) return again(T.not_yours);
    m.textContent = T.could_not_wake;
    s.textContent = T.try_again;
  } catch (e) {
    m.textContent = T.unreachable;
    s.textContent = T.try_again;
  }
})();
</script></body></html>"""

# the page's lines, English and 简体中文; the relay's refusal messages are not shown, the
# page says the same thing in the visitor's language
WAKE_STRINGS = {
    "en": {
        "waking": "Waking your Muse…",
        "moment": "A few seconds.",
        "again_before": "Sign in again at ",
        "again_after": ".",
        "no_token": "Open your Muse from where you signed in.",
        "not_yours": "That link is no longer yours.",
        "could_not_wake": "Could not wake your Muse.",
        "unreachable": "Could not reach nanoMuse.",
        "try_again": "Try again in a moment.",
    },
    "zh": {
        "waking": "正在唤醒你的 Muse…",
        "moment": "几秒钟就好。",
        "again_before": "请在 ",
        "again_after": " 重新登录。",
        "no_token": "请从你登录的地方打开你的 Muse。",
        "not_yours": "这个链接已经不属于你了。",
        "could_not_wake": "没能唤醒你的 Muse。",
        "unreachable": "连不上 nanoMuse。",
        "try_again": "请稍后再试。",
    },
}


def wake_page(signin_url: str, zh: bool = False) -> str:
    lang = "zh" if zh else "en"
    strings = WAKE_STRINGS[lang]
    return (
        WAKE_PAGE.replace("__LANG__", lang)
        .replace("__WAKING__", strings["waking"])
        .replace("__MOMENT__", strings["moment"])
        .replace("__STRINGS__", json.dumps(strings, ensure_ascii=True))
        .replace("__SIGNIN__", signin_url)
    )
