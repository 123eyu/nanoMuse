import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import { cx } from "../util";

/**
 * The lightest feedback a chat can take (relay 0.31): two thumbs under the newest reply, and
 * one question after a Stop (why) that can be skipped. Both go to the runtime
 * (`POST /api/threads/{id}/feedback`), which keeps the mark on the reply and tells the account's
 * record one word (`turn.feedback`, `turn.stopped`) when it is signed in. Nothing here reads the
 * reply or sends a word of the conversation. Android: ui/feedback/FeedbackCards.kt; desktop:
 * src/client/Feedback.tsx; iOS: NanoMuseFeedback.swift.
 */

export const DOWN_REASONS = ["wrong", "unhelpful", "unsafe", "slow", "other"] as const;
export const STOP_REASONS = ["wrong", "slow", "changed_mind", "done", "other"] as const;
/** How long after a Stop the question is still worth asking. */
export const STOP_ASK_MS = 90_000;

export type Vote = "up" | "down" | "";

// ----------------------------------------------------------------------------- the Stop that was just pressed
export interface StopAsk {
  thread: string;
  at: number;
}

let stopAsk: StopAsk | null = null;
const listeners = new Set<() => void>();
function emit() {
  for (const l of listeners) l();
}

/** The person pressed Stop in `thread` and the run was running: the question is worth asking for a while. */
export function noteStopped(thread: string, now = Date.now()) {
  stopAsk = { thread, at: now };
  emit();
}

/** The question was answered, skipped, or the moment passed. */
export function clearStopAsk(ask: StopAsk | null = stopAsk) {
  if (ask && stopAsk && ask.thread === stopAsk.thread && ask.at === stopAsk.at) {
    stopAsk = null;
    emit();
  }
}

/** The Stop being asked about in `thread`, while it is still fresh. */
export function stopAskFor(thread: string, now = Date.now()): StopAsk | null {
  return stopAsk && stopAsk.thread === thread && now - stopAsk.at <= STOP_ASK_MS ? stopAsk : null;
}

export function useStopAsk(thread: string): StopAsk | null {
  const ask = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => stopAsk,
    () => stopAsk,
  );
  return ask && ask.thread === thread ? ask : null;
}

/** For tests. */
export function resetStopAsk() {
  stopAsk = null;
  emit();
}

// ----------------------------------------------------------------------------- the words
export function downReasonText(t: (k: string) => string, reason: string): string {
  switch (reason) {
    case "wrong":
      return t("Wrong");
    case "unhelpful":
      return t("Not helpful");
    case "unsafe":
      return t("Should not have done that");
    case "slow":
      return t("Too slow");
    default:
      return t("Another reason");
  }
}

export function stopReasonText(t: (k: string) => string, reason: string): string {
  switch (reason) {
    case "wrong":
      return t("It was going wrong");
    case "slow":
      return t("Too slow");
    case "changed_mind":
      return t("Changed my mind");
    case "done":
      return t("It was already done");
    default:
      return t("Another reason");
  }
}

const pill = "rounded-full bg-surface-2 px-3 py-1 text-[12px] text-fg hover:bg-surface-2/70";
const quiet = "text-[12px] text-muted";

// ----------------------------------------------------------------------------- the thumbs
/**
 * Two small thumbs under a reply: drawn under the newest one, and kept under an older one
 * that was voted on. A thumb down opens one line of reasons, each a code, with Skip; a thumb
 * up says "noted" for a moment. The same thumb again takes the mark back.
 */
export function Thumbs({ thread, eventId, vote: kept, newest, className }: { thread: string; eventId: string; vote: Vote; newest: boolean; className?: string }) {
  const t = useT();
  const [asking, setAsking] = useState(false);
  const [noted, setNoted] = useState(false);
  const [busy, setBusy] = useState(false);
  // the thumb fills on the click; the runtime's `update` frame then carries the mark into the event
  const [cast_, setCast] = useState<{ was: Vote; now: Vote } | null>(null);
  const vote: Vote = cast_ && cast_.was === kept ? cast_.now : kept;
  useEffect(() => {
    if (!noted) return;
    const h = setTimeout(() => setNoted(false), 2500);
    return () => clearTimeout(h);
  }, [noted]);
  if (!newest && !vote && !kept) return null;
  const cast = async (next: Vote, reason = "") => {
    if (busy) return;
    setBusy(true);
    setCast({ was: kept, now: next });
    try {
      await api.feedback(thread, { event: eventId, vote: next, reason });
    } catch {
      setCast(null); // a mark that did not land is not worth a toast; the thumb goes back
    } finally {
      setBusy(false);
    }
  };
  const up = () => {
    setAsking(false);
    const next: Vote = vote === "up" ? "" : "up";
    setNoted(next === "up");
    void cast(next);
  };
  const down = () => {
    setNoted(false);
    const next: Vote = vote === "down" ? "" : "down";
    setAsking(next === "down");
    void cast(next);
  };
  return (
    <div className={cx("ml-1 mt-0.5 mb-1", className)}>
      <div className="flex items-center gap-0.5">
        <button type="button" aria-label={t("Good reply")} aria-pressed={vote === "up"} onClick={up} className={cx("rounded-full p-1.5", vote === "up" ? "text-fg" : "text-muted/70 hover:text-fg")}>
          <ThumbsUp size={14} fill={vote === "up" ? "currentColor" : "none"} />
        </button>
        <button type="button" aria-label={t("Not a good reply")} aria-pressed={vote === "down"} onClick={down} className={cx("rounded-full p-1.5", vote === "down" ? "text-fg" : "text-muted/70 hover:text-fg")}>
          <ThumbsDown size={14} fill={vote === "down" ? "currentColor" : "none"} />
        </button>
        {noted && <span className={cx(quiet, "ml-1")}>{t("Noted, thanks")}</span>}
      </div>
      {asking && vote === "down" && (
        <div className="mt-1 ml-1.5">
          <div className={quiet}>{t("What was wrong?")}</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {DOWN_REASONS.map((r) => (
              <button
                key={r}
                type="button"
                className={pill}
                onClick={() => {
                  setAsking(false);
                  setNoted(true);
                  void cast("down", r);
                }}
              >
                {downReasonText(t, r)}
              </button>
            ))}
            <button type="button" className={cx(quiet, "px-2 py-1")} onClick={() => setAsking(false)}>
              {t("Skip")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------- the question after a Stop
/**
 * One question after a Stop, in the chat it happened in: why, as one of five codes, or Skip.
 * Drawn where the chat's cards go while the question is still fresh, then gone on its own.
 */
export function StopAskCard({ ask, className }: { ask: StopAsk; className?: string }) {
  const t = useT();
  useEffect(() => {
    const left = Math.max(0, STOP_ASK_MS - (Date.now() - ask.at));
    const h = setTimeout(() => clearStopAsk(ask), left); // unanswered for long enough: the moment has passed
    return () => clearTimeout(h);
  }, [ask]);
  const answer = (reason: string) => {
    clearStopAsk(ask);
    api.feedback(ask.thread, { stop_reason: reason }).catch(() => undefined);
  };
  return (
    <section className={cx("rounded-[20px] border border-border bg-surface px-3.5 py-2.5", className)}>
      <div className="text-[14px] font-medium">{t("Why did you stop it?")}</div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {STOP_REASONS.map((r) => (
          <button key={r} type="button" className={pill} onClick={() => answer(r)}>
            {stopReasonText(t, r)}
          </button>
        ))}
        <button type="button" className={cx(quiet, "px-2 py-1")} onClick={() => answer("skipped")}>
          {t("Skip")}
        </button>
      </div>
    </section>
  );
}
