"""The desktop app's operator as the runtime's hands (nanomuse.computer.operator): the
HTTP contract against a fake server, the backend choice, the link's screen → act → event
chain with the picture mapped onto the operator's screen, and the same mapping for the
Python fallback backends with fake sizes."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from nanomuse.computer import hands as hands_mod
from nanomuse.computer import operator as op
from nanomuse.computer.link import ComputerLink
from nanomuse.config import GUISettings, HandsSettings, Settings
from nanomuse.phone.link import DeviceError
from nanomuse.tools.computer import ComputerAct, ComputerScreen
from tests.test_computer import FakeHands
from tests.test_phone import png

TOKEN = "t0ken-for-tests"


class FakeOperator:
    """What harness/desktop/src/operator-server.ts answers, on a thread."""

    def __init__(
        self,
        width: int = 3840,
        height: int = 2160,
        available: bool = True,
        reason: str = "",
        helper: dict[str, Any] | None = None,
        windows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.width, self.height = width, height
        self.available, self.reason = available, reason
        self.executed: list[dict[str, Any]] = []
        self.shots: list[dict[str, Any]] = []
        self.fail_next = ""
        # (status, error) the next /screenshot answers with — the operator's 403 on a Mac
        # without Screen Recording (harness/desktop/src/operator.ts capture())
        self.refuse_shot: tuple[int, str] | None = None
        # macOS: the app's helper as /info describes it ({present, running, ...}), and the
        # windows it lists for /windows; /window captures one of them at twice its points
        self.helper = helper
        self.windows = list(windows or [])
        self.window_shots: list[dict[str, Any]] = []
        self.refuse_window: tuple[int, str] | None = None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return None

            def _send(self, code: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authed(self) -> bool:
                if self.headers.get("Authorization") == f"Bearer {TOKEN}":
                    return True
                self._send(401, {"error": "bad token"})
                return False

            def do_GET(self) -> None:  # noqa: N802 — http.server's name
                if not self._authed():
                    return
                if self.path == "/windows":
                    if fake.helper is None:
                        return self._send(503, {"error": "window mode needs nanoMuse Computer Use"})
                    if fake.refuse_window is not None and fake.refuse_window[0] != 404:
                        code, why = fake.refuse_window  # a 404 is one window's, not the list's
                        return self._send(code, {"error": why})
                    return self._send(200, {"windows": fake.windows})
                if self.path != "/info":
                    return self._send(404, {"error": "no such route"})
                self._send(
                    200,
                    {
                        "available": fake.available,
                        "reason": fake.reason,
                        "platform": "linux",
                        "display": {"width": fake.width, "height": fake.height, "scaleFactor": 2},
                        **({"helper": fake.helper} if fake.helper is not None else {}),
                    },
                )

            def do_POST(self) -> None:  # noqa: N802
                if not self._authed():
                    return
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/screenshot":
                    fake.shots.append(body)
                    if fake.refuse_shot is not None:
                        code, why = fake.refuse_shot
                        return self._send(code, {"error": why})
                    w, h = (
                        int(body.get("width") or fake.width),
                        int(body.get("height") or fake.height),
                    )
                    return self._send(
                        200,
                        {
                            "base64": png(w, h),
                            "mime": "image/png",
                            "width": w,
                            "height": h,
                            "screen": {"width": fake.width, "height": fake.height},
                            "scaleFactor": 2,
                            "display": {
                                "id": "0",
                                "bounds": {
                                    "x": 0,
                                    "y": 0,
                                    "width": fake.width // 2,
                                    "height": fake.height // 2,
                                },
                            },
                        },
                    )
                if self.path == "/execute":
                    if fake.fail_next:
                        why, fake.fail_next = fake.fail_next, ""
                        return self._send(400, {"error": why})
                    fake.executed.append(body)
                    return self._send(200, {"ok": True, "note": ""})
                if self.path == "/window":
                    fake.window_shots.append(body)
                    if fake.refuse_window is not None:
                        code, why = fake.refuse_window
                        return self._send(code, {"error": why})
                    found = [w for w in fake.windows if w["id"] == body.get("id")]
                    if not found:
                        return self._send(
                            404,
                            {
                                "error": f"window {body.get('id')} could not be captured: "
                                f"no window with id {body.get('id')} is on screen"
                            },
                        )
                    x, y, w, h = found[0]["bounds"]
                    return self._send(
                        200,
                        {
                            "base64": png(int(w) * 2, int(h) * 2),
                            "mime": "image/png",
                            "width": int(w) * 2,
                            "height": int(h) * 2,
                            "window": {
                                "id": found[0]["id"],
                                "x": x,
                                "y": y,
                                "width": w,
                                "height": h,
                            },
                            "scale": 2,
                        },
                    )
                self._send(404, {"error": "no such route"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def operator(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeOperator]:
    fake = FakeOperator()
    monkeypatch.setenv(op.URL_ENV, fake.url)
    monkeypatch.setenv(op.TOKEN_ENV, TOKEN)
    monkeypatch.setattr("nanomuse.computer.link.active_window", lambda: ("gedit", "Untitled"))
    yield fake
    fake.close()


# ----------------------------------------------------------------------------- the client
def test_operator_hands_speak_the_contract(operator: FakeOperator) -> None:
    hands = op.OperatorHands.from_env()
    assert hands.name == "desktop" and hands.size() == (3840, 2160)
    hands.click(10, 20)
    hands.click(10, 20, button="right")
    hands.click(10, 20, clicks=2)
    hands.move(1, 2)
    hands.drag(1, 2, 3, 4)
    hands.scroll(5, 6, -300)
    hands.type("héllo 中文")
    hands.key(["ctrl", "s"])
    assert [e["action"] for e in operator.executed] == [
        "click", "right_click", "double_click", "move", "drag", "scroll", "type", "key",
    ]  # fmt: skip
    assert operator.executed[0] == {"action": "click", "x": 10, "y": 20}
    assert operator.executed[4] == {"action": "drag", "x": 1, "y": 2, "x2": 3, "y2": 4}
    assert operator.executed[6]["text"] == "héllo 中文" and operator.executed[7]["keys"] == [
        "ctrl",
        "s",
    ]
    operator.fail_next = "no such key: hyperspace"
    with pytest.raises(RuntimeError, match="hyperspace"):
        hands.key(["hyperspace"])
    # the window routes are the helper's: without it the operator says so, with the status
    assert hands.helper_present is False
    with pytest.raises(op.OperatorError, match="window mode needs nanoMuse Computer Use") as exc:
        hands.client.windows()
    assert exc.value.status == 503
    # the picture: the size the runtime asks for, the screen's size next to it
    raw = op.operator_capture(hands.client, 1600)
    assert raw is not None
    assert (raw["width"], raw["height"]) == (1596, 896)
    assert (raw["screen_w"], raw["screen_h"]) == (3840, 2160)
    assert raw["mime"] == "image/png" and raw["screenshot"]
    assert operator.shots[-1] == {
        "format": "jpeg",
        "quality": 80,
        "max_pixels": 2_000_000,
        "width": 1596,
        "height": 896,
    }
    # a PNG without a size: the operator decides the size under the pixel budget
    hands.client.screenshot(fmt="png", max_pixels=0)
    assert operator.shots[-1] == {"format": "png", "quality": 80, "max_pixels": 0}


def test_operator_env_and_token(operator: FakeOperator, monkeypatch: pytest.MonkeyPatch) -> None:
    assert op.operator_env() == (operator.url, TOKEN)
    monkeypatch.setenv(op.TOKEN_ENV, "wrong")
    with pytest.raises(hands_mod.HandsUnavailable, match="bad token"):
        op.OperatorHands.from_env()
    monkeypatch.delenv(op.URL_ENV)
    assert op.operator_env() is None
    with pytest.raises(hands_mod.HandsUnavailable, match="not running"):
        op.OperatorHands.from_env()
    # a server that is gone
    monkeypatch.setenv(op.URL_ENV, "http://127.0.0.1:9")
    with pytest.raises(hands_mod.HandsUnavailable, match="not reachable"):
        op.OperatorHands.from_env()


def test_backend_choice_prefers_the_operator(
    operator: FakeOperator, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert hands_mod.pick_backend("auto").name == "desktop"
    assert hands_mod.pick_backend("desktop").name == "desktop"
    # the operator says no (a Wayland session): its reason leads, the Python backends follow
    # — a Linux rule; on a Mac the operator is the only path (the test below), so the
    # platform is pinned here for the macOS runner
    monkeypatch.setattr(op, "_platform", lambda: "linux")
    operator.available, operator.reason = False, "Wayland session: no global screen or cursor"
    monkeypatch.setattr(hands_mod.shutil, "which", lambda name: None)
    monkeypatch.setitem(hands_mod.sys.modules, "pyautogui", None)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    with pytest.raises(hands_mod.HandsUnavailable, match="Wayland session") as exc:
        hands_mod.pick_backend("auto")
    assert "xdotool" in str(exc.value)  # the fallbacks were tried and said why too
    with pytest.raises(
        hands_mod.HandsUnavailable, match="^Wayland session: no global screen or cursor$"
    ):
        hands_mod.pick_backend("desktop")
    info = hands_mod.describe_availability("auto")
    assert info["available"] is False and info["reason"].startswith("Wayland session")


def test_wayland_session_is_read_from_the_environment() -> None:
    linux = hands_mod.sys.platform.startswith("linux")
    assert hands_mod.wayland_session({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}) is linux
    assert hands_mod.wayland_session({"XDG_SESSION_TYPE": "Wayland"}) is linux
    assert hands_mod.wayland_session({"WAYLAND_DISPLAY": "wayland-0"}) is linux
    # XWayland by hand (both set, no session type): the X11 tools reach X windows — allowed
    assert hands_mod.wayland_session({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":1"}) is False
    assert hands_mod.wayland_session({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}) is False
    assert hands_mod.wayland_session({}) is False


@pytest.mark.skipif(not hands_mod.sys.platform.startswith("linux"), reason="a Linux rule")
async def test_linux_wayland_says_the_hands_are_off(
    settings: Settings, operator: FakeOperator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Wayland login on Linux: the operator says no, and the runtime does not try xdotool
    or pyautogui behind its back (XWayland would take them and move nothing visible), nor
    does it hand a grab of the XWayland root on as the screen — the first attempt says
    plainly that the hands are off here, with the words of Settings → Computer use."""
    monkeypatch.setattr(op, "_platform", lambda: "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("DISPLAY", ":0")
    operator.available, operator.reason = False, hands_mod.WAYLAND_TEXT
    operator.refuse_shot = (503, f"no screenshot: {hands_mod.WAYLAND_TEXT}")

    def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a Python backend was tried under Wayland")

    monkeypatch.setattr(hands_mod, "_import_pyautogui", never)
    monkeypatch.setattr(hands_mod, "XdotoolHands", never)
    monkeypatch.setattr("nanomuse.computer.link.capture", never)
    with pytest.raises(hands_mod.HandsUnavailable) as exc:
        hands_mod.pick_backend("auto")
    assert str(exc.value) == hands_mod.WAYLAND_TEXT
    assert hands_mod.describe_availability("auto")["reason"] == hands_mod.WAYLAND_TEXT
    link = ComputerLink(HandsSettings(enabled=True, settle_s=0.0))
    with pytest.raises(DeviceError, match=r"^the hands are off on this computer: Wayland session"):
        await link.screen()
    # the same without the desktop app around (the CLI on a Wayland desktop): the same words
    monkeypatch.delenv(op.URL_ENV)
    with pytest.raises(hands_mod.HandsUnavailable) as exc:
        hands_mod.pick_backend("auto")
    assert str(exc.value) == hands_mod.WAYLAND_TEXT
    link2 = ComputerLink(HandsSettings(enabled=True, settle_s=0.0))
    with pytest.raises(DeviceError, match="the hands are off on this computer: Wayland session"):
        await link2.screen()


# ----------------------------------------------------------------------------- one path on a Mac
MAC_SCREEN_TEXT = (
    "macOS: switch on nanoMuse Desktop under System Settings → Privacy & Security → "
    "Screen Recording, then quit and reopen the app."
)


def _never_capture(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("the Python capture layer was touched on a Mac under the desktop app")


def test_operator_owns_the_screen_on_a_mac_under_the_app(
    operator: FakeOperator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(op, "_platform", lambda: "darwin")
    assert op.operator_owns_the_screen() is True
    monkeypatch.setattr(op, "_platform", lambda: "linux")
    assert op.operator_owns_the_screen() is False
    monkeypatch.setattr(op, "_platform", lambda: "darwin")
    monkeypatch.delenv(op.URL_ENV)
    assert op.operator_owns_the_screen() is False  # no app around: the Python backends as before


def test_mac_backend_choice_is_the_operator_or_its_reason(
    operator: FakeOperator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Mac without Screen Recording: the operator says no, and that is the answer —
    pyautogui is never tried (it would be a second TCC prompt, for the runtime)."""
    monkeypatch.setattr(op, "_platform", lambda: "darwin")
    operator.available, operator.reason = False, MAC_SCREEN_TEXT

    def no_pyautogui() -> Any:
        raise AssertionError("pyautogui was tried on a Mac under the desktop app")

    monkeypatch.setattr(hands_mod, "_import_pyautogui", no_pyautogui)
    with pytest.raises(hands_mod.HandsUnavailable) as exc:
        hands_mod.pick_backend("auto")
    assert str(exc.value) == MAC_SCREEN_TEXT
    info = hands_mod.describe_availability("auto")
    assert info == {"available": False, "backend": None, "reason": MAC_SCREEN_TEXT}
    # the same Mac with the app's server gone: still no Python fallback, the reason says so
    monkeypatch.setenv(op.URL_ENV, "http://127.0.0.1:9")
    with pytest.raises(hands_mod.HandsUnavailable, match="not reachable"):
        hands_mod.pick_backend("auto")
    # Linux keeps the fallbacks (the Wayland test above): the operator's reason leads, the rest follow
    monkeypatch.setattr(op, "_platform", lambda: "linux")
    monkeypatch.setenv(op.URL_ENV, operator.url)
    monkeypatch.setitem(hands_mod.sys.modules, "pyautogui", None)
    monkeypatch.setattr(hands_mod.shutil, "which", lambda name: None)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    with pytest.raises(hands_mod.HandsUnavailable, match="xdotool"):
        hands_mod.pick_backend("auto")


async def test_mac_screenshot_never_falls_back_to_python(
    settings: Settings, operator: FakeOperator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(op, "_platform", lambda: "darwin")
    monkeypatch.setattr("nanomuse.computer.link.capture", _never_capture)
    monkeypatch.setattr("nanomuse.computer.link.screen_size", _never_capture)
    # 1. the operator refuses the picture (403, the permission text): the tool's error, in its words
    link = ComputerLink(HandsSettings(enabled=True, settle_s=0.0))
    assert (link.device.width, link.device.height) == (3840, 2160)
    operator.refuse_shot = (403, f"no screenshot: the picture is black — {MAC_SCREEN_TEXT}")
    with pytest.raises(DeviceError, match="Screen Recording, then quit and reopen") as exc:
        await link.screen()
    assert "could not take a screenshot of this computer" in str(exc.value)
    operator.refuse_shot = None
    screen = await link.screen()
    assert (screen.width, screen.height) == (1596, 896)
    # 2. the operator is not available at all (the permission missing at start): no Python
    #    capture, the operator's reason — and no mss for the screen size either
    operator.available, operator.reason = False, MAC_SCREEN_TEXT
    link2 = ComputerLink(HandsSettings(enabled=True, settle_s=0.0))
    assert link2.connected is False
    with pytest.raises(DeviceError, match=r"^could not take a screenshot of this computer: macOS"):
        await link2.screen()
    assert link2.status()["reason"] == MAC_SCREEN_TEXT
    # 3. the app's server is gone: still nothing from Python; the size waits for the first look
    monkeypatch.setenv(op.URL_ENV, "http://127.0.0.1:9")
    link3 = ComputerLink(HandsSettings(enabled=True, settle_s=0.0))
    assert (link3.device.width, link3.device.height) == (0, 0)
    with pytest.raises(DeviceError, match="not reachable"):
        await link3.screen()
    # 4. Linux with the operator saying no (and no Python hands either): the Python capture
    #    is the fallback for the picture, as before
    monkeypatch.setattr(op, "_platform", lambda: "linux")
    monkeypatch.setenv(op.URL_ENV, operator.url)
    monkeypatch.setitem(hands_mod.sys.modules, "pyautogui", None)
    monkeypatch.setattr(hands_mod.shutil, "which", lambda name: None)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    raw = {
        "app": "gedit", "app_name": "gedit", "width": 1596, "height": 896,
        "screen_w": 3840, "screen_h": 2160, "keyboard": False, "screenshot": png(64, 40),
    }  # fmt: skip
    monkeypatch.setattr("nanomuse.computer.link.capture", lambda max_width=1600: dict(raw))
    monkeypatch.setattr("nanomuse.computer.link.screen_size", lambda: (3840, 2160))
    link4 = ComputerLink(HandsSettings(enabled=True, settle_s=0.0))
    screen = await link4.screen()
    assert (screen.width, screen.height) == (1596, 896)


# ----------------------------------------------------------------------------- the link
async def test_link_sees_the_picture_and_moves_in_the_screen(
    settings: Settings, operator: FakeOperator
) -> None:
    events: list[dict[str, Any]] = []
    link = ComputerLink(
        HandsSettings(enabled=True, settle_s=0.0),
        shots_dir=settings.agent.workspace / "screenshots",
        on_event=events.append,
    )
    # before the first look: the operator's screen, asked over HTTP (never the Python capture)
    assert (link.device.width, link.device.height) == (3840, 2160)
    assert link.connected and link.status()["backend"] == "desktop"
    screen = await link.screen()
    # the model sees a 1596×896 picture of the 3840×2160 screen
    assert (screen.width, screen.height) == (1596, 896)
    assert screen.image_size == (1596, 896) and screen.app == "gedit"
    assert link.mapping is not None and link.mapping.screen_w == 3840
    status = link.status()
    assert status["screen_size"] == [3840, 2160] and status["picture_size"] == [1596, 896]
    # a click in the middle of the picture lands in the middle of the screen
    await link.act({"action": "click", "x": 798, "y": 448, "label": "Save"})
    sent = operator.executed[-1]
    assert sent["action"] == "click" and (sent["x"], sent["y"]) == (1920.0, 1080.0)
    event = next(e for e in events if e["event"] == "act")
    assert (event["x"], event["y"]) == (1920.0, 1080.0) and (event["fx"], event["fy"]) == (0.5, 0.5)
    assert event["app"] == "gedit" and event["label"] == "Save"
    # drags and scrolls scale the same way; one pixel past the edge means the edge
    await link.act({"action": "drag", "x": 0, "y": 0, "x2": 1700, "y2": 896})
    sent = operator.executed[-1]
    assert (sent["x2"], sent["y2"]) == (3840.0, 2160.0)
    await link.act({"action": "scroll", "x": 798, "y": 448, "dy": -100})
    assert operator.executed[-1]["dy"] == pytest.approx(-100 * 2160 / 896, abs=0.01)
    # the operator's refusal is the tool's error, in its words
    operator.fail_next = "Accessibility is not granted"
    with pytest.raises(DeviceError, match="Accessibility"):
        await link.act({"action": "click", "x": 1, "y": 1, "label": "x"})


async def test_tools_take_boxes_and_the_norm_grid(
    settings: Settings, operator: FakeOperator
) -> None:
    link = ComputerLink(
        HandsSettings(enabled=True, settle_s=0.0), shots_dir=settings.agent.workspace / "shots"
    )
    look = await ComputerScreen(link=link).execute()
    assert look.ok and "1596×896" in look.output
    assert "pixels of this 1596×896 picture, (0,0) top-left" in look.output
    assert "3840" not in look.output  # the display's own size is not the model's business
    act = ComputerAct(link=link, gui=GUISettings())
    r = await act.execute(action="click", box=[780, 430, 816, 466], label="Save")
    assert r.ok, r.error
    assert (operator.executed[-1]["x"], operator.executed[-1]["y"]) == (1920.0, 1080.0)
    r = await act.execute(action="drag", box=[0, 0, 0, 0], box2=[1596, 896, 1596, 896])
    assert r.ok and (operator.executed[-1]["x2"], operator.executed[-1]["y2"]) == (3840.0, 2160.0)
    r = await act.execute(action="click", box=[1, 2, 3], label="x")
    assert not r.ok and "[x1, y1, x2, y2]" in (r.error or "")
    r = await act.execute(action="click", x=1700, y=10, label="x")
    assert not r.ok and "outside the 1596×896 picture" in (r.error or "")
    # the drag's end is checked like its start; NaN and infinity are refused as numbers
    r = await act.execute(action="drag", x=10, y=10, x2=1700, y2=10)
    assert not r.ok and "outside the 1596×896 picture" in (r.error or "")
    r = await act.execute(action="drag", x=10, y=10, x2=float("nan"), y2=10)
    assert not r.ok and "finite" in (r.error or "")
    # the 0–1000 grid: 500,500 is the picture's centre whatever its size
    link.settings.coords = "norm1000"
    r = await act.execute(action="click", x=500, y=500, label="Save")
    assert r.ok and (operator.executed[-1]["x"], operator.executed[-1]["y"]) == (1920.0, 1080.0)
    r = await act.execute(action="click", box=[0, 0, 1000, 1000], label="Save")
    assert r.ok and (operator.executed[-1]["x"], operator.executed[-1]["y"]) == (1920.0, 1080.0)


# ----------------------------------------------------------------------------- the fallback
class SizedHands(FakeHands):
    """A Python backend that knows its screen space (pyautogui's `size()`)."""

    name = "sized"

    def __init__(self, width: int, height: int) -> None:
        super().__init__()
        self._size = (width, height)

    def size(self) -> tuple[int, int]:
        return self._size


async def test_fallback_backends_map_the_picture_onto_their_space(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Retina Mac without the desktop app: mss captures 2880×1800 pixels, the picture is
    1596×996, pyautogui moves in 1440×900 points — the hands' space wins over the capture's."""
    monkeypatch.delenv(op.URL_ENV, raising=False)
    raw = {
        "app": "Safari", "app_name": "Safari", "width": 1596, "height": 996,
        "screen_w": 2880, "screen_h": 1800, "keyboard": False, "screenshot": png(64, 40),
    }  # fmt: skip
    monkeypatch.setattr("nanomuse.computer.link.capture", lambda max_width=1600: dict(raw))
    monkeypatch.setattr("nanomuse.computer.link.active_window", lambda: ("Safari", "Safari"))
    monkeypatch.setattr("nanomuse.computer.link.screen_size", lambda: (2880, 1800))
    events: list[dict[str, Any]] = []
    hands = SizedHands(1440, 900)
    link = ComputerLink(
        HandsSettings(enabled=True, settle_s=0.0), on_event=events.append, backend=hands
    )
    screen = await link.screen()
    assert (screen.width, screen.height) == (1596, 996)
    assert (link.device.width, link.device.height) == (1440, 900)
    await link.act({"action": "click", "x": 798, "y": 498, "label": "Reload"})
    kind, x, y, *_ = hands.calls[-1]
    assert kind == "click" and (round(x), round(y)) == (720, 450)
    event = next(e for e in events if e["event"] == "act")
    assert (event["fx"], event["fy"]) == (0.5, 0.5)
    # a backend with no say over its space (xdotool on X11 behaves like this): the capture's
    plain = FakeHands()
    link2 = ComputerLink(HandsSettings(enabled=True, settle_s=0.0), backend=plain)
    await link2.screen()
    assert (link2.device.width, link2.device.height) == (2880, 1800)
    await link2.act({"action": "click", "x": 798, "y": 498, "label": "Reload"})
    kind, x, y, *_ = plain.calls[-1]
    assert (round(x), round(y)) == (1440, 900)
