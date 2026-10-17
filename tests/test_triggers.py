from __future__ import annotations

import asyncio
import imaplib
import socket
import threading
from pathlib import Path

import pytest

from nanomuse.schema import RiskLevel
from nanomuse.tools import Triggers
from nanomuse.triggers import TriggerStore, matches


def test_matches_needs_every_word_anywhere():
    assert matches("", "anything")
    assert matches("landlord", "From: The Landlord <l@landlord.example>", "Subject: rent")
    assert matches("landlord rent", "The Landlord", "about the RENT")
    assert not matches("landlord rent", "The Landlord", "about the flat")
    assert matches("房东", "房东 <fd@example.cn>", "本月房租")
    assert matches("  Review   ", "Design review")


def test_store_creates_fires_once_per_key_and_cancels(tmp_path: Path):
    store = TriggerStore(tmp_path / "t.db")
    with pytest.raises(ValueError):
        store.create("sms", "x")
    with pytest.raises(ValueError):
        store.create("mail", "   ")
    with pytest.raises(ValueError):
        store.create("event", "brief me", lead_minutes=99999)

    mail = store.create(
        "mail", "summarise it and draft a reply", match=" landlord  rent ", thread="t_1"
    )
    assert mail.kind == "mail" and mail.match == "landlord rent" and mail.thread == "t_1"
    assert (
        mail.status == "active" and mail.fired == 0 and mail.secret == "" and mail.lead_minutes == 0
    )
    assert mail.describe() == "mail matching “landlord rent”"

    event = store.create("event", "put together a one-page brief", match="review", lead_minutes=45)
    assert event.lead_minutes == 45 and event.describe() == "45 min before events matching “review”"
    assert store.create("event", "x").describe() == "30 min before any event"

    hook = store.create("hook", "check the site is up", match="deploy")
    assert len(hook.secret) >= 20 and hook.to_dict()["secret"] == hook.secret
    assert mail.to_dict()["secret"] == ""  # only a hook has a key
    assert hook.describe() == "webhook “deploy”"

    # a key fires once
    assert store.mark_fired(mail.id, "uid:41")
    assert not store.mark_fired(mail.id, "uid:41")
    assert store.mark_fired(mail.id, "uid:42")
    assert store.has_fired(mail.id, "uid:41") and not store.has_fired(mail.id, "uid:43")
    got = store.get(mail.id)
    assert got is not None and got.fired == 2 and got.last_fired_at
    assert store.forget_fired_before("2999-01-01T00:00:00+00:00") == 2
    assert not store.has_fired(mail.id, "uid:41")

    assert [t.id for t in store.active("mail")] == [mail.id]
    assert len(store.list("active")) == 4
    cancelled = store.cancel(event.id)
    assert cancelled is not None and cancelled.status == "cancelled"
    assert store.cancel(event.id) is None and store.cancel("t_nope") is None
    assert [t.id for t in store.active("event")] != [event.id]
    assert store.delete(hook.id) and store.get(hook.id) is None
    assert store.get_meta("mail_uid", "0") == "0"
    store.set_meta("mail_uid", "77")
    store.set_meta("mail_uid", "78")
    assert store.get_meta("mail_uid") == "78"
    assert mail.render().startswith(f"[{mail.id}] when mail matching")
    store.close()


def test_triggers_tool(tmp_path: Path):
    store = TriggerStore(tmp_path / "t.db")
    changes: list[int] = []
    tool = Triggers(store=store)
    tool.thread_of = lambda: "t_9"
    tool.on_change = lambda: changes.append(1)
    tool.base_url = "http://phone.local:8787"
    tool.available = lambda: {"mail": False, "event": True, "hook": True}
    run = asyncio.run

    assert run(tool.execute(action="create", kind="mail", text="x")).error
    assert "email connector" in (
        run(tool.execute(action="create", kind="mail", text="x")).error or ""
    )
    assert run(tool.execute(action="create", kind="event", text="")).error
    assert run(tool.execute(action="create", text="x")).error  # no kind
    assert run(tool.execute(action="list")).output == "No triggers."

    r = run(
        tool.execute(
            action="create", kind="event", match="review", text="brief me", lead_minutes=20
        )
    )
    assert not r.error and r.output.startswith("Trigger set.")
    items = store.list("active")
    assert len(items) == 1 and items[0].thread == "t_9" and items[0].lead_minutes == 20
    assert changes == [1]

    r = run(tool.execute(action="create", kind="hook", match="deploy", text="check the site"))
    hook = store.active("hook")[0]
    assert f"http://phone.local:8787/api/hooks/{hook.id}?key={hook.secret}" in r.output

    listed = run(tool.execute(action="list")).output
    assert items[0].id in listed and hook.id in listed
    assert run(tool.execute(action="cancel")).error
    assert run(tool.execute(action="cancel", trigger_id="t_nope")).error
    assert run(tool.execute(action="cancel", trigger_id=hook.id)).output.startswith("Cancelled.")
    assert store.active("hook") == [] and changes == [1, 1, 1]
    assert run(tool.execute(action="rename")).error

    create = tool.assess(
        {"action": "create", "kind": "mail", "match": "landlord", "text": "draft a reply"}
    )
    assert create.summary == "triggers create: mail “landlord”: draft a reply"
    # creating standing work is a change of state; listing and cancelling are not
    assert create.risk == RiskLevel.MODERATE
    assert tool.assess({"action": "list"}).risk == RiskLevel.SAFE
    assert tool.assess({"action": "cancel", "trigger_id": "t_x"}).risk == RiskLevel.SAFE
    store.close()


# ----------------------------------------------------------------------------- IMAP watcher
class _TinyImap(threading.Thread):
    """Just enough IMAP for the watcher: greeting, CAPABILITY, LOGIN, EXAMINE, UID SEARCH,
    UID FETCH (RFC822), LOGOUT. Messages are ``{uid: raw_bytes}``."""

    def __init__(self, messages: dict[int, bytes]):
        super().__init__(daemon=True)
        self.messages = messages
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.commands: list[str] = []

    def close(self) -> None:
        """Wait for the one conversation to end and close the listening socket."""
        self.join(timeout=5)
        self.sock.close()

    def run(self) -> None:
        conn, _ = self.sock.accept()
        f = conn.makefile("rwb")

        def send(line: str) -> None:
            f.write(line.encode() + b"\r\n")
            f.flush()

        send("* OK tiny ready")
        while True:
            raw = f.readline()
            if not raw:
                break
            line = raw.decode().rstrip("\r\n")
            self.commands.append(line)
            tag, _, rest = line.partition(" ")
            cmd = rest.upper()
            if cmd.startswith("CAPABILITY"):
                send("* CAPABILITY IMAP4rev1")
                send(f"{tag} OK done")
            elif cmd.startswith("LOGIN"):
                send(f"{tag} OK logged in" if "me@example.com" in rest else f"{tag} NO bad")
            elif cmd.startswith(("EXAMINE", "SELECT")):
                send(f"* {len(self.messages)} EXISTS")
                send(f"{tag} OK [READ-ONLY] done")
            elif cmd.startswith("UID SEARCH"):
                send("* SEARCH " + " ".join(str(u) for u in sorted(self.messages)))
                send(f"{tag} OK done")
            elif cmd.startswith("UID FETCH"):
                uid = int(rest.split()[2])
                body = self.messages[uid]
                f.write(
                    f"* 1 FETCH (UID {uid} RFC822 {{{len(body)}}}\r\n".encode() + body + b")\r\n"
                )
                f.flush()
                send(f"{tag} OK done")
            elif cmd.startswith("LOGOUT"):
                send("* BYE")
                send(f"{tag} OK bye")
                break
            else:
                send(f"{tag} BAD what")
        f.close()
        conn.close()


def _mail(sender: str, subject: str, body: str) -> bytes:
    return (
        f"From: {sender}\r\nTo: me@example.com\r\nSubject: {subject}\r\n"
        f"Date: Tue, 22 Sep 2026 10:00:00 +0800\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"
    ).encode()


async def test_mail_watcher_reads_new_messages_by_uid(monkeypatch: pytest.MonkeyPatch):
    from nanomuse.config import EmailSettings
    from nanomuse.triggers.mail import MailWatcher

    messages = {
        40: _mail("Old <old@example.com>", "Before you connected", "not replayed"),
        41: _mail(
            "The Landlord <l@example.com>",
            "=?utf-8?b?5oi/56ef?= from October",
            "Hi, the rent goes up by 3%.\r\nYour verification code is 123456.",
        ),
        42: _mail("Newsletter <n@example.com>", "This week in tea", "..."),
    }
    server = _TinyImap(messages)
    server.start()
    seen_timeouts: list[float | None] = []

    def plain_imap(host: str, port: int, timeout: float | None = None) -> imaplib.IMAP4:
        seen_timeouts.append(timeout)
        return imaplib.IMAP4(host, port, timeout=timeout)

    monkeypatch.setattr(imaplib, "IMAP4_SSL", plain_imap)
    settings = EmailSettings(
        enabled=True,
        imap_host="127.0.0.1",
        imap_port=server.port,
        address="me@example.com",
        password="pw",
    )
    watcher = MailWatcher(settings)
    assert watcher.configured

    # first look: only the high-water mark
    fresh, mark = await watcher.look(0)
    assert fresh == [] and mark == 42
    assert any(c.upper().endswith("EXAMINE INBOX") for c in server.commands)  # read-only
    assert not any("FETCH" in c.upper() for c in server.commands)
    assert seen_timeouts == [30.0], "the poll loop never waits on a silent server for good"
    server.close()

    server = _TinyImap(messages)
    server.start()
    settings.imap_port = server.port
    fresh, mark = await watcher.look(40)
    server.close()
    assert mark == 42 and [m.uid for m in fresh] == [41, 42]
    landlord = fresh[0]
    assert (
        landlord.sender == "The Landlord <l@example.com>"
        and landlord.subject == "房租 from October"
    )
    assert "rent goes up by 3%" in landlord.body and "123456" not in landlord.body  # scrubbed
    assert landlord.key == "uid:41" and landlord.render().startswith("From: The Landlord")

    # a bad login is an error the poller can show, not a crash
    server = _TinyImap(messages)
    server.start()
    settings.imap_port = server.port
    settings.address = "someone@else.example"
    with pytest.raises(RuntimeError, match="IMAP error"):
        await watcher.look(40)
    server.close()
    # no credentials at all
    with pytest.raises(RuntimeError, match="not set"):
        await MailWatcher(
            EmailSettings(
                enabled=True, imap_host="x", address="{{vault:EMAIL_ADDRESS}}", password="pw"
            )
        ).look(0)


# ------------------------------------------------------------------ the fourth kind (#486)
def test_device_trigger_describes_and_fires_once_per_arrival(tmp_path: Path):
    store = TriggerStore(tmp_path / "t.db")
    with pytest.raises(ValueError):
        store.create("device", "   ")

    device = store.create(
        "device", "tell me when the phone is back", match=" pixel  phone ", thread="t_1"
    )
    assert device.kind == "device" and device.match == "pixel phone"
    assert device.lead_minutes == 0 and device.secret == ""  # like mail: no lead, no key
    assert device.describe() == "device “pixel phone” coming online"
    assert device.to_dict()["kind"] == "device"

    # one arrival is one key: the same arrival never fires twice
    assert store.mark_fired(device.id, "device:phone-1:a1")
    assert not store.mark_fired(device.id, "device:phone-1:a1")
    assert store.mark_fired(device.id, "device:phone-1:a2")
    assert store.get(device.id).fired == 2  # type: ignore[union-attr]
    assert [t.id for t in store.active("device")] == [device.id]
    assert (
        store.list("active", "device")[0]
        .render()
        .startswith(f"[{device.id}] when device “pixel phone” coming online")
    )
    assert store.create("device", "say hello").describe() == "any device coming online"
    store.close()


def test_device_trigger_needs_the_hub_and_says_so(tmp_path: Path):
    store = TriggerStore(tmp_path / "t.db")
    tool = Triggers(store=store)
    tool.thread_of = lambda: "main"
    run = asyncio.run

    tool.available = lambda: {"mail": True, "event": True, "hook": True, "device": False}
    refused = run(tool.execute(action="create", kind="device", text="tell me", match="pixel"))
    assert refused.error and "hub" in (refused.error or "")
    assert store.list(None) == []

    tool.available = lambda: {"mail": True, "event": True, "hook": True, "device": True}
    made = run(tool.execute(action="create", kind="device", text="tell me", match="pixel"))
    assert not made.error and made.output.startswith("Trigger set.")
    assert "device “pixel” coming online" in made.output
    assert "arrival" in made.output  # the model is told how often it can fire
    # it is standing work, so creating it is a change of state
    assert (
        tool.assess(
            {"action": "create", "kind": "device", "match": "pixel", "text": "tell me"}
        ).risk
        == RiskLevel.MODERATE
    )
    store.close()


def test_arrivals_seeds_the_first_frame_then_fires_on_the_transition():
    """The hub's `devices` frame: what was already online when the app started is not an
    arrival; a device that drops and comes back, or joins the account later, is."""
    from nanomuse.triggers import arrivals

    me = {"id": "desk-1", "name": "Desk", "kind": "computer", "online": True}
    phone = {"id": "phone-1", "name": "Pixel", "kind": "phone", "online": True}

    state, came = None, None  # type: ignore[assignment]
    state, came = arrivals(state, [me, phone], "desk-1")  # the welcome frame: seeds only
    assert came == []

    state, came = arrivals(state, [me, {**phone, "online": False}], "desk-1")  # away
    assert came == []
    state, came = arrivals(state, [me, phone], "desk-1")  # back
    assert [d["id"] for d in came] == ["phone-1"]

    # this computer never announces itself
    state, came = arrivals(state, [{**me, "online": False}, phone], "desk-1")
    assert came == []
    state, came = arrivals(state, [me, phone], "desk-1")
    assert came == []

    # a device that joins the account after the first frame counts as an arrival
    tablet = {"id": "tab-1", "name": "Tab", "kind": "tablet", "online": True}
    state, came = arrivals(state, [me, phone, tablet], "desk-1")
    assert [d["id"] for d in came] == ["tab-1"]
    # and an unchanged list brings nothing
    state, came = arrivals(state, [me, phone, tablet], "desk-1")
    assert came == []
