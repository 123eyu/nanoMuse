import { act } from "react";
import { create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { setLocaleSetting } from "../i18n";
import { clearStopAsk, noteStopped, resetStopAsk, STOP_ASK_MS, StopAskCard, stopAskFor, Thumbs } from "./Feedback";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const feedback = vi.fn(async () => ({ ok: true }));
vi.mock("../api", () => ({
  api: { feedback: (...args: unknown[]) => feedback(...(args as [])) },
}));

function byLabel(r: ReactTestRenderer, label: string) {
  return r.root.findAllByProps({ "aria-label": label })[0];
}
function byText(r: ReactTestRenderer, text: string) {
  return r.root.findAllByProps({ type: "button" }).find((b) => b.props.children === text);
}

// The lightest feedback: a thumb is one request with the reply's id and the vote; a thumb
// down asks why in codes; the same thumb again takes the mark back. Thumbs are drawn under
// the newest reply only, except where a mark was already left.

describe("Thumbs", () => {
  beforeAll(() => setLocaleSetting("en"));
  beforeEach(() => feedback.mockClear());

  it("sends the vote with the reply's id, and takes it back with an empty vote", async () => {
    let r!: ReactTestRenderer;
    act(() => {
      r = create(<Thumbs thread="main" eventId="ev1" vote="" newest />);
    });
    await act(async () => byLabel(r, "Good reply").props.onClick());
    expect(feedback).toHaveBeenCalledWith("main", { event: "ev1", vote: "up", reason: "" });
    act(() => r.update(<Thumbs thread="main" eventId="ev1" vote="up" newest />));
    await act(async () => byLabel(r, "Good reply").props.onClick());
    expect(feedback).toHaveBeenLastCalledWith("main", { event: "ev1", vote: "", reason: "" });
  });

  it("asks why after a thumb down, and sends the reason as a code", async () => {
    let r!: ReactTestRenderer;
    act(() => {
      r = create(<Thumbs thread="main" eventId="ev1" vote="" newest />);
    });
    await act(async () => byLabel(r, "Not a good reply").props.onClick());
    expect(feedback).toHaveBeenCalledWith("main", { event: "ev1", vote: "down", reason: "" });
    act(() => r.update(<Thumbs thread="main" eventId="ev1" vote="down" newest />));
    const reason = byText(r, "Should not have done that");
    expect(reason).toBeDefined();
    await act(async () => reason!.props.onClick());
    expect(feedback).toHaveBeenLastCalledWith("main", { event: "ev1", vote: "down", reason: "unsafe" });
    expect(byText(r, "Should not have done that")).toBeUndefined();
  });

  it("is drawn under the newest reply, or where a mark was left", () => {
    let r!: ReactTestRenderer;
    act(() => {
      r = create(<Thumbs thread="main" eventId="old" vote="" newest={false} />);
    });
    expect(r.toJSON()).toBeNull();
    act(() => r.update(<Thumbs thread="main" eventId="old" vote="down" newest={false} />));
    expect(byLabel(r, "Not a good reply")).toBeDefined();
  });
});

describe("the question after a Stop", () => {
  beforeAll(() => setLocaleSetting("en"));
  beforeEach(() => {
    feedback.mockClear();
    resetStopAsk();
  });
  afterEach(() => vi.useRealTimers());

  it("is asked in the chat that was stopped, while it is fresh", () => {
    noteStopped("side", 1_000);
    expect(stopAskFor("side", 2_000)).toEqual({ thread: "side", at: 1_000 });
    expect(stopAskFor("main", 2_000)).toBeNull();
    expect(stopAskFor("side", 1_000 + STOP_ASK_MS + 1)).toBeNull();
    clearStopAsk();
    expect(stopAskFor("side", 2_000)).toBeNull();
  });

  it("sends one code, Skip included, and is gone", async () => {
    vi.useFakeTimers();
    noteStopped("main", Date.now());
    const ask = stopAskFor("main")!;
    let r!: ReactTestRenderer;
    act(() => {
      r = create(<StopAskCard ask={ask} />);
    });
    await act(async () => byText(r, "Changed my mind")!.props.onClick());
    expect(feedback).toHaveBeenCalledWith("main", { stop_reason: "changed_mind" });
    expect(stopAskFor("main")).toBeNull();

    noteStopped("main", Date.now());
    const again = stopAskFor("main")!;
    act(() => r.update(<StopAskCard ask={again} />));
    await act(async () => byText(r, "Skip")!.props.onClick());
    expect(feedback).toHaveBeenLastCalledWith("main", { stop_reason: "skipped" });
    expect(stopAskFor("main")).toBeNull();
  });

  it("goes on its own once the moment has passed", () => {
    vi.useFakeTimers();
    noteStopped("main", Date.now());
    const ask = stopAskFor("main")!;
    act(() => {
      create(<StopAskCard ask={ask} />);
    });
    act(() => {
      vi.advanceTimersByTime(STOP_ASK_MS + 10);
    });
    expect(stopAskFor("main", Date.now() - STOP_ASK_MS)).toBeNull();
    expect(feedback).not.toHaveBeenCalled();
  });
});
