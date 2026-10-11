"""Phone numbers and e-mail addresses: normalise, mask, hash.

The relay never stores the identifier itself. `id_hash` (HMAC-SHA256 with the
deployment secret) is the account key; `hint` is what the account page shows.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_CN_MOBILE = re.compile(r"^1[3-9]\d{9}$")
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class BadIdentifier(ValueError):
    pass


@dataclass(frozen=True)
class Identifier:
    channel: str  # phone | email
    value: str  # +8613800138000 / user@example.com (normalised)

    @property
    def hint(self) -> str:
        if self.channel == "phone":
            digits = self.value
            if digits.startswith("+86") and len(digits) == 14:
                n = digits[3:]
                return f"{n[:3]}****{n[-4:]}"
            return f"{digits[:4]}****{digits[-3:]}"
        local, _, domain = self.value.partition("@")
        shown = local[0] if len(local) <= 2 else local[:2]
        return f"{shown}***@{domain}"

    def hash(self, key: bytes) -> str:
        return hmac.new(key, f"{self.channel}:{self.value}".encode(), hashlib.sha256).hexdigest()


def parse(raw: str) -> Identifier:
    s = (raw or "").strip()
    if not s:
        raise BadIdentifier("empty")
    if "@" in s:
        s = s.lower()
        if not _EMAIL.match(s) or len(s) > 254:
            raise BadIdentifier("email")
        return Identifier("email", s)
    digits = re.sub(r"[\s\-()]", "", s)
    if digits.startswith("0086"):
        digits = "+" + digits[2:]
    elif digits.startswith("86") and len(digits) == 13:
        digits = "+" + digits
    if _CN_MOBILE.match(digits):
        return Identifier("phone", "+86" + digits)
    if _E164.match(digits):
        return Identifier("phone", digits)
    raise BadIdentifier("phone")
