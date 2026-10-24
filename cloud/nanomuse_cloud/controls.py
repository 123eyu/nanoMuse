"""0.22: the operator's switches and thresholds — the *Controls* page of the console.

Five switches, each on unless the operator turned it off, kept in the database (not the
environment) so a flip applies at once and survives a restart:

- ``free_allowance`` — off: every limited account is refused at the model endpoints with a
  ``429 allowance_exhausted`` whose body says ``paused: true`` (the apps already draw that
  shape; members keep working, they are on the operator's own bill by choice);
- ``signups`` — off: an identifier without an account gets ``403 signup_closed`` before a
  code is sent; existing accounts sign in as before;
- ``cloud_service`` — off: everything but the health check, the public config, the console
  and the admin API answers ``503 service_paused``; open hub sockets are closed;
- ``sync`` — off: the ``/v1/sync/*`` endpoints answer ``503 sync_paused``;
- ``hub`` — off: ``/v1/hub`` closes with 4003 ``hub_paused``, ``/v1/devices`` answers 503.

A sixth, ``apps_only`` (0.28), is a policy rather than a pause: on (the default), the models
answer the nanoMuse apps, the runtime and the web console only, and a request from other
software (a pool worker, an SDK, the unbranded upstream app) is refused with
``403 client_not_supported``; members are exempt. Off, any client with a valid key is
answered. It is not reported under ``paused`` (the apps are never affected by it).

Thresholds: "when the number of accounts reaches N, do X" — several rules, each with N,
an action (close sign-ups, pause the free allowance, pause sync, notify the operator by
e-mail), enabled or not. A rule fires once (``last_fired_at``) until the operator re-arms
it; they are evaluated when an account is created and once a minute. Everything that
happens here is written to ``control_audit`` (who, when, what) and shown on the page.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

from .config import Settings
from .db import Database
from .senders import SendError, compose_notice_mail, smtp_send

log = logging.getLogger("nanomuse_cloud.controls")

SWITCHES: tuple[str, ...] = ("free_allowance", "signups", "cloud_service", "sync", "hub", "apps_only")
# the switches whose "off" pauses a part of the service, which the apps are told about
PAUSABLE: tuple[str, ...] = ("free_allowance", "signups", "cloud_service", "sync", "hub")

# a rule's action → the switch it turns off (None: notify only)
ACTIONS: dict[str, str | None] = {
    "close_signups": "signups",
    "pause_allowance": "free_allowance",
    "pause_sync": "sync",
    "notify": None,
}

RULE_MAX_THRESHOLD = 10_000_000


class ControlError(ValueError):
    """A request the page or the CLI made that cannot be carried out; the message is plain."""


class Controls:
    def __init__(self, db: Database, settings: Settings, mailer: Callable[[EmailMessage], None] | None = None):
        self.db = db
        self.s = settings
        self._mailer = mailer
        # notices a sign-in queued instead of sending (evaluate(defer_mail=True)): sent by
        # flush_notices, on a thread of its own or by the minute timer
        self._pending: list[tuple[str, list[str], str]] = []
        self._pending_lock = threading.Lock()
        self._on: dict[str, bool] = dict.fromkeys(SWITCHES, True)
        for key, row in db.controls_all().items():
            if key in self._on:
                self._on[key] = bool(row["enabled"])
        off = [k for k, v in self._on.items() if not v]
        if off:
            log.warning("controls: off at start: %s", ", ".join(off))

    # -- the switches ---------------------------------------------------------------------------

    def on(self, key: str) -> bool:
        return self._on.get(key, True)

    def paused(self) -> dict[str, bool]:
        """What the apps may know: which parts of the service are off, as ``{switch: true}``.
        ``apps_only`` is not among them: it never affects the apps."""
        return {k: not self._on[k] for k in PAUSABLE}

    def state(self) -> dict[str, Any]:
        rows = self.db.controls_all()
        out: dict[str, Any] = {}
        for key in SWITCHES:
            r = rows.get(key)
            out[key] = {
                "enabled": self._on[key],
                "updated_at": int(r["updated_at"]) if r is not None else None,
                "actor": str(r["actor"]) if r is not None else "",
                "note": str(r["note"]) if r is not None else "",
            }
        return out

    def set(self, key: str, enabled: bool, actor: str = "console", note: str = "") -> dict[str, Any]:
        """Flip one switch. Written, applied at once, audited — also when nothing changed
        (the line then says so), because the operator asked and should see it happened."""
        if key not in SWITCHES:
            raise ControlError(f"Unknown switch {key!r}; one of {', '.join(SWITCHES)}")
        actor = (actor or "console")[:80]
        before = self._on[key]
        self._on[key] = bool(enabled)
        self.db.control_put(key, bool(enabled), actor, note)
        word = "on" if enabled else "off"
        detail = f"{key} {word}" + ("" if before != bool(enabled) else " (already)") + (f" · {note}" if note else "")
        self.db.audit_add(actor, "switch", key, detail)
        self.db.add_event("", "control.switch", f"{actor}: {detail}")
        (log.warning if not enabled else log.info)("controls: %s turned %s by %s%s", key, word, actor, f" ({note})" if note else "")
        return self.state()[key]

    # -- the rules ------------------------------------------------------------------------------

    @staticmethod
    def _rule_dict(r: Any) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "threshold": int(r["threshold"]),
            "action": str(r["action"]),
            "enabled": bool(r["enabled"]),
            "note": str(r["note"] or ""),
            "created_at": int(r["created_at"]),
            "last_fired_at": int(r["last_fired_at"]) if r["last_fired_at"] else None,
            "fired_accounts": int(r["fired_accounts"]) if r["fired_accounts"] is not None else None,
        }

    def rules(self) -> list[dict[str, Any]]:
        return [self._rule_dict(r) for r in self.db.rules_all()]

    @staticmethod
    def _check_rule(body: dict, partial: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if "threshold" in body or not partial:
            try:
                n = int(body.get("threshold"))
            except (TypeError, ValueError) as e:
                raise ControlError("threshold is a whole number of accounts") from e
            if not 1 <= n <= RULE_MAX_THRESHOLD:
                raise ControlError(f"threshold is between 1 and {RULE_MAX_THRESHOLD}")
            out["threshold"] = n
        if "action" in body or not partial:
            action = str(body.get("action") or "")
            if action not in ACTIONS:
                raise ControlError(f"action is one of {', '.join(ACTIONS)}")
            out["action"] = action
        if "enabled" in body:
            out["enabled"] = 1 if body.get("enabled") else 0
        elif not partial:
            out["enabled"] = 1
        if "note" in body:
            out["note"] = str(body.get("note") or "")[:200]
        return out

    def add_rule(self, body: dict, actor: str = "console") -> dict[str, Any]:
        fields = self._check_rule(body)
        rule_id = self.db.rule_add(fields["threshold"], fields["action"], bool(fields["enabled"]), fields.get("note", ""))
        detail = f"#{rule_id}: at {fields['threshold']} accounts → {fields['action']}" + ("" if fields["enabled"] else " (disabled)")
        self.db.audit_add(actor, "rule.add", str(rule_id), detail)
        row = self.db.rule(rule_id)
        assert row is not None
        return self._rule_dict(row)

    def update_rule(self, rule_id: int, body: dict, actor: str = "console") -> dict[str, Any]:
        """Change a rule; ``{"rearm": true}`` clears its last firing so it can fire again. A
        changed threshold re-arms it too — the operator moved the line, so it has not been
        reached yet in the new sense."""
        row = self.db.rule(rule_id)
        if row is None:
            raise ControlError("No such rule")
        fields = self._check_rule(body, partial=True)
        if body.get("rearm") or ("threshold" in fields and fields["threshold"] != int(row["threshold"])):
            fields["last_fired_at"] = None
            fields["fired_accounts"] = None
        if not fields:
            raise ControlError("Nothing to change")
        self.db.rule_update(rule_id, **fields)
        said = ", ".join(f"{k}={'cleared' if v is None else v}" for k, v in fields.items())
        self.db.audit_add(actor, "rule.update", str(rule_id), f"#{rule_id}: {said}")
        row = self.db.rule(rule_id)
        assert row is not None
        return self._rule_dict(row)

    def delete_rule(self, rule_id: int, actor: str = "console") -> None:
        row = self.db.rule(rule_id)
        if row is None:
            raise ControlError("No such rule")
        self.db.rule_delete(rule_id)
        self.db.audit_add(actor, "rule.delete", str(rule_id), f"#{rule_id}: at {int(row['threshold'])} → {row['action']}")

    def evaluate(self, accounts_total: int, *, defer_mail: bool = False) -> list[dict[str, Any]]:
        """Fire every enabled, not-yet-fired rule whose threshold the count has reached.
        Returns the rules that fired. Called when an account is created and once a minute.
        A switch flips at once either way; with ``defer_mail`` the e-mail of a *notify*
        rule is queued for ``flush_notices`` instead of sent here, so the sign-in that
        crossed the line does not wait on SMTP."""
        fired: list[dict[str, Any]] = []
        for r in self.db.rules_all():
            if not r["enabled"] or r["last_fired_at"] is not None or accounts_total < int(r["threshold"]):
                continue
            rule = self._rule_dict(r)
            self._fire(rule, accounts_total, defer_mail)
            fired.append(rule)
        return fired

    def _fire(self, rule: dict[str, Any], total: int, defer_mail: bool = False) -> None:
        actor = f"rule:{rule['id']}"
        t = int(time.time())
        self.db.rule_update(rule["id"], last_fired_at=t, fired_accounts=int(total))
        switch = ACTIONS.get(rule["action"])
        detail = f"#{rule['id']}: {total} accounts ≥ {rule['threshold']} → {rule['action']}"
        self.db.audit_add(actor, "rule.fired", str(rule["id"]), detail)
        self.db.add_event("", "control.rule", detail)
        log.warning("controls: %s", detail)
        if switch is not None:
            self.set(switch, False, actor=actor, note=f"rule #{rule['id']} at {total} accounts")
        if rule["action"] == "notify":
            subject = f"{total} accounts: rule #{rule['id']} fired"
            lines = [
                f"The relay at {self.s.public_base} has {total} accounts; rule #{rule['id']} (threshold {rule['threshold']}) asked to be told.",
                f"中继 {self.s.public_base} 的账号数达到 {total}（规则 #{rule['id']}，阈值 {rule['threshold']}）。",
                f"Time (UTC): {time.strftime('%Y-%m-%d %H:%M', time.gmtime(t))}",
                f"Console: {self.s.public_base.rstrip('/')}/app/admin/#controls",
            ]
            if defer_mail:
                with self._pending_lock:
                    self._pending.append((subject, lines, actor))
            else:
                self.notify(subject, lines, actor=actor)

    def flush_notices(self) -> int:
        """Send what ``evaluate(defer_mail=True)`` queued; how many went. Safe to call from
        any thread and when nothing is queued."""
        with self._pending_lock:
            batch, self._pending = self._pending, []
        return sum(1 for subject, lines, actor in batch if self.notify(subject, lines, actor=actor))

    def flush_notices_later(self) -> bool:
        """Send the queued notices on a thread of their own, so the request that queued
        them answers now. The minute timer flushes too, in case this thread never ran."""
        with self._pending_lock:
            if not self._pending:
                return False
        threading.Thread(target=self.flush_notices, name="nm-notices", daemon=True).start()
        return True

    # -- notify ---------------------------------------------------------------------------------

    @property
    def can_notify(self) -> bool:
        return bool(self.s.admin_email and (self._mailer is not None or (self.s.smtp_host and self.s.smtp_from)))

    def notify(self, subject: str, lines: list[str], actor: str = "console") -> bool:
        """An e-mail to ADMIN_EMAIL through the SMTP settings; the audit says whether it went.
        Nothing personal rides along — counts and the relay's own address only."""
        if not self.s.admin_email:
            self.db.audit_add(actor, "notify", "", f"not sent: ADMIN_EMAIL is not set · {subject}")
            log.warning("controls: nobody to notify (ADMIN_EMAIL unset): %s", subject)
            return False
        msg = compose_notice_mail(self.s.smtp_from or "no-reply@localhost", self.s.admin_email, subject, lines)
        try:
            if self._mailer is not None:
                self._mailer(msg)
            else:
                smtp_send(self.s, msg)
        except SendError as e:
            self.db.audit_add(actor, "notify", "", f"failed ({e}) · {subject}")
            return False
        self.db.audit_add(actor, "notify", "", f"sent · {subject}")
        return True

    # -- the audit ------------------------------------------------------------------------------

    def audit(self, limit: int = 200) -> list[dict[str, Any]]:
        return [
            {
                "id": int(r["id"]),
                "ts": int(r["ts"]),
                "actor": str(r["actor"]),
                "ip": str(r["ip"] or ""),
                "action": str(r["action"]),
                "target": str(r["target"] or ""),
                "detail": str(r["detail"] or ""),
            }
            for r in self.db.audit_recent(limit)
        ]

    def view(self, accounts_total: int) -> dict[str, Any]:
        """Everything the Controls page shows in one answer."""
        rules = self.rules()
        pending = [r for r in rules if r["enabled"] and r["last_fired_at"] is None]
        return {
            "switches": self.state(),
            "rules": rules,
            "accounts_total": accounts_total,
            "next_threshold": min((r["threshold"] for r in pending), default=None),
            "notify_configured": self.can_notify,
            "admin_email_set": bool(self.s.admin_email),
            "audit": self.audit(),
        }
