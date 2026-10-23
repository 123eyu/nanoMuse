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
_GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})


class BadIdentifier(ValueError):
    pass


def _canonical_email(address: str) -> str:
    """Canonical form of an e-mail address for hashing and account identity.

    Gmail and Googlemail addresses have ``googlemail.com`` folded to
    ``gmail.com``, dots removed and ``+tag`` stripped from the local part,
    matching how Gmail routes mail. Other providers decide for themselves
    whether plus addressing is an alias, so their local parts stay exact.
    """
    local, _, domain = address.partition("@")
    if domain in _GMAIL_DOMAINS:
        domain = "gmail.com"
        if "+" in local:
            local = local.split("+", 1)[0]
        local = local.replace(".", "")
    if not local:
        raise BadIdentifier("email")
    canonical = f"{local}@{domain}"
    if not _EMAIL.match(canonical) or len(canonical) > 254:
        raise BadIdentifier("email")
    return canonical


@dataclass(frozen=True)
class Identifier:
    channel: str  # phone | email
    value: str  # +8613800138000 / user@example.com (canonical)
    raw_value: str = ""  # as typed (lower-cased for email), when different from `value`

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

    def legacy_hash(self, key: bytes) -> str | None:
        """The pre-canonical hash when the typed address had dots, a ``+tag`` or
        ``googlemail.com``, so an existing account from before canonicalisation
        is still found when its holder signs in."""
        if not self.raw_value or self.raw_value == self.value:
            return None
        return hmac.new(key, f"{self.channel}:{self.raw_value}".encode(), hashlib.sha256).hexdigest()

    def hashes(self, key: bytes) -> tuple[str, ...]:
        """The canonical hash first, followed by the legacy hash when distinct."""
        primary = self.hash(key)
        legacy = self.legacy_hash(key)
        return (primary, legacy) if legacy and legacy != primary else (primary,)

    def merged_hash(self, key: bytes) -> str | None:
        """Relay 0.26 stripped ``+tag`` from every address, so ``dev+1@example.com`` signed
        in to ``dev@example.com``'s account. 0.27 keeps such addresses apart, but an
        account that already exists under the stripped form is still reached by the
        tagged address (never adopted by it), so nobody loses an account to the change."""
        if self.channel != "email":
            return None
        local, _, domain = self.value.partition("@")
        if "+" not in local or domain == "gmail.com":
            return None
        stripped = f"{local.split('+', 1)[0]}@{domain}"
        return hmac.new(key, f"{self.channel}:{stripped}".encode(), hashlib.sha256).hexdigest()


def parse(raw: str) -> Identifier:
    s = (raw or "").strip()
    if not s:
        raise BadIdentifier("empty")
    if "@" in s:
        s = s.lower()
        if not _EMAIL.match(s) or len(s) > 254:
            raise BadIdentifier("email")
        canonical = _canonical_email(s)
        return Identifier("email", canonical, raw_value=s if canonical != s else "")
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
