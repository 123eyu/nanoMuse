/**
 * The protected exit: what runs between a person asking to quit (later, to
 * restart for an update) and the quit itself. One flow, one busy judgment, for
 * both: close the gate so no new work starts under the hand-over, ask before
 * ending what is running, with keeping it running as the default, and only
 * on an explicit confirmation end the work, wait for it to end, and complete
 * the exit. Cancelling opens the gate again and nothing has stopped.
 *
 * Closing the window to stay in the tray, sitting idle, a crash and the system
 * switching off never come through here: they are not an exit request, and
 * `approval` stays `none` for them: the property a later install-on-exit
 * change must not talk itself out of. `approval` says only that the person
 * confirmed this exit; it does not say the stop finished every piece of work.
 * When the stop's deadline or the rounds ran out with work still on, the log
 * carries "work still on after the stop": the install-on-exit change reads
 * that situation as the person having confirmed ending it, not as quiet.
 */

export type ExitIntent = "quit" | "restart-update";
/** Whether the exit went through this flow with the person's explicit say-so. */
export type ExitApproval = "none" | "user";

/** One line of the work a real exit would end, as the Host's aggregate reports it. */
export interface WorkItem {
  kind: "reply" | "computer" | "task" | "approval";
  label: string;
}

export interface WorkSource {
  /** The work a real exit would end, over every source the Host aggregates. */
  busy(): Promise<WorkItem[]>;
  /** Open or close the gate: closed, new work is refused until it opens again. */
  setGate(open: boolean): Promise<void>;
  /** End the work in progress; resolves once it has ended. */
  stopAll(): Promise<void>;
}

export interface ExitDialog {
  /** What would end, in the person's words; true when they confirmed ending it. */
  confirm(items: WorkItem[], intent: ExitIntent): Promise<boolean>;
}

export interface ProtectedExitOptions {
  work: WorkSource;
  dialog: ExitDialog;
  /** Completes the exit once the work has ended (the shell's `app.quit()`); never called when the person keeps running. */
  finish(): void;
  log(line: string): void;
  /** Tests; the process's timers by default. */
  setTimer?(fn: () => void, ms: number): NodeJS.Timeout;
  clearTimer?(timer: NodeJS.Timeout): void;
  /** How long the stop may take before the exit carries on anyway; the Host's own stop ends what is left. */
  stopTimeoutMs?: number;
}

const STOP_TIMEOUT_MS = 15_000;
/** After the confirmed stop, this many busy checks that still find work end the exit anyway. */
const STOP_ROUNDS = 3;

export class ProtectedExit {
  private phase: "idle" | "handoff" = "idle";
  private approved: ExitApproval = "none";
  private readonly setTimer: NonNullable<ProtectedExitOptions["setTimer"]>;
  private readonly clearTimer: NonNullable<ProtectedExitOptions["clearTimer"]>;

  constructor(private readonly options: ProtectedExitOptions) {
    this.setTimer = options.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = options.clearTimer ?? ((timer) => clearTimeout(timer));
  }

  /** Whether the person's exit went through the flow to its end (never for a crash or a system shutdown). */
  get approval(): ExitApproval {
    return this.approved;
  }

  /** An exit is being asked for right now (the final busy check, the question or the stop). */
  get underWay(): boolean {
    return this.phase === "handoff";
  }

  /**
   * Ask to exit with the work protected: the gate closes first, the work is
   * named and, unless the person keeps running, ended and waited for. True
   * when the exit proceeds, `finish` has run by then.
   */
  async requestExit(intent: ExitIntent): Promise<boolean> {
    if (this.phase !== "idle") {
      this.options.log(`exit: a request is already under way (${intent})`);
      return false;
    }
    this.phase = "handoff";
    try {
      await this.attempt(this.options.work.setGate(false), "closing the gate");
      let items = await this.busyNow();
      this.options.log(`exit: the gate is closed and ${items.length ? `${items.length} kind(s) of work are on` : "nothing is on"} (${intent})`);
      if (items.length === 0) {
        await this.attempt(this.options.work.setGate(true), "opening the gate");
        return this.complete();
      }
      let confirmed = false;
      try {
        confirmed = await this.options.dialog.confirm(items, intent);
      } catch (exc) {
        this.options.log(`exit: the question failed (${String(exc)}); keeping the work running`);
      }
      if (!confirmed) {
        await this.attempt(this.options.work.setGate(true), "opening the gate");
        this.options.log("exit: the person keeps running; the gate is open and the work goes on");
        this.phase = "idle";
        return false;
      }
      for (let round = 0; round < STOP_ROUNDS; round++) {
        if (round > 0) this.options.log(`exit: work still on after round ${round}; stopping again`);
        await this.attempt(this.withDeadline(this.options.work.stopAll()), "the stop");
        items = await this.busyNow();
        if (items.length === 0) break;
      }
      if (items.length > 0) this.options.log(`exit: work still on after the stop (${items.map((i) => i.kind).join(", ")}); the exit stops the Host, which ends it`);
      return this.complete();
    } finally {
      this.phase = "idle";
    }
  }

  /** The busy check, tolerant: a Host that answers nothing reads as nothing on. */
  private async busyNow(): Promise<WorkItem[]> {
    const items = await this.attempt(this.options.work.busy(), "the busy check");
    return Array.isArray(items) ? (items as WorkItem[]) : [];
  }

  /** The exit goes on: the approval is the person's, and the shell does what is left of quitting. */
  private complete(): boolean {
    this.approved = "user";
    this.phase = "idle";
    this.options.finish();
    return true;
  }

  /** The stop, bounded: a Host that never answers must not hold the exit open. */
  private withDeadline(stopping: Promise<void>): Promise<void> {
    const ms = this.options.stopTimeoutMs ?? STOP_TIMEOUT_MS;
    return new Promise<void>((resolve) => {
      let done = false;
      const settle = () => {
        if (done) return;
        done = true;
        this.clearTimer(timer);
        resolve();
      };
      const timer = this.setTimer(() => {
        this.options.log(`exit: the stop did not answer within ${Math.round(ms / 1000)} s; carrying on`);
        settle();
      }, ms);
      void stopping.then(settle, (exc) => {
        this.options.log(`exit: the stop failed (${String(exc)})`);
        settle();
      });
    });
  }

  /** A step the exit tolerates failing: the answer is logged and read as "nothing". */
  private async attempt(promise: Promise<unknown>, what: string): Promise<unknown> {
    try {
      return await promise;
    } catch (exc) {
      this.options.log(`exit: ${what} failed (${String(exc)}); the exit reads it as nothing`);
      return undefined;
    }
  }
}
