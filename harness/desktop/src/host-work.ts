/**
 * The work the Host's plugin reports for the protected exit (its `src/work.ts`),
 * over the plugin's own guarded API: what a real quit would end right now, the
 * gate new work respects while the exit hands over, and the stop the confirmed
 * exit asks for. The shell authenticates the way a browser does: the launch
 * URL's one-time token mints a session cookie once (`GET /?token=…` answers
 * 303 with it), and the cookie answers for the requests after.
 *
 * A Host that is down, or an older plugin without these routes, answers
 * nothing: the check then reads as idle and the quit proceeds as it did before
 * this existed. That is safe to accept because the plugin ships in the same
 * installer as the shell: the pairing is the norm, the mismatch the dev case.
 */

import type { WorkItem } from "./protected-exit";

export type { WorkItem };

export interface HostWorkOptions {
  log(line: string): void;
  /** Tests; the process's fetch by default. */
  fetchImpl?: typeof fetch;
}

/** How long one request may take; the Host is on the loopback. */
const TIMEOUT_MS = 5000;
const WORK_PREFIX = "/nanomuse/cloud/work";

const KINDS = new Set(["reply", "computer", "task", "approval"]);

export class HostWork {
  private cookie: string | null = null;

  constructor(
    private readonly launchUrl: string,
    private readonly options: HostWorkOptions,
  ) {}

  /** The Host's origin (the routes sit under the port the launch URL names). */
  private origin(): string {
    return new URL(this.launchUrl).origin;
  }

  /** The session cookie, minted from the launch URL's token when there is none yet (or `again`). */
  private async mintCookie(again = false): Promise<string | null> {
    if (this.cookie && !again) return this.cookie;
    this.cookie = null;
    try {
      const res = await (this.options.fetchImpl ?? fetch)(this.launchUrl, { redirect: "manual", signal: AbortSignal.timeout(TIMEOUT_MS) });
      for (const header of res.headers.getSetCookie()) {
        // the name is dsh-auth-<authority>; the value runs to the first attribute
        const pair = header.split(";", 1)[0] ?? "";
        if (!pair.startsWith("dsh-auth-") || !pair.includes("=")) continue;
        this.cookie = pair;
        break;
      }
      if (!this.cookie) this.options.log(`work: the Host did not mint a session cookie (status ${res.status})`);
    } catch (exc) {
      this.options.log(`work: the Host did not mint a session cookie: ${message(exc)}`);
    }
    return this.cookie;
  }

  /** One request with the cookie; a 401 mints the cookie again and tries once more. Null when the Host answered nothing usable. */
  private async request(path: string, init?: RequestInit): Promise<Response | null> {
    const doFetch = this.options.fetchImpl ?? fetch;
    let cookie = await this.mintCookie();
    if (!cookie) return null;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const res = await doFetch(`${this.origin()}${path}`, { ...init, headers: { ...(init?.headers as Record<string, string> | undefined), cookie }, signal: AbortSignal.timeout(TIMEOUT_MS) });
        if (res.status === 401 && attempt === 0) {
          cookie = await this.mintCookie(true);
          if (!cookie) return null;
          continue;
        }
        return res;
      } catch (exc) {
        this.options.log(`work: ${init?.method ?? "GET"} ${path}: ${message(exc)}`);
        return null;
      }
    }
    return null;
  }

  /** The work a real exit would end, over every source the plugin aggregates; empty when there is no answer. */
  async busy(): Promise<WorkItem[]> {
    const res = await this.request(WORK_PREFIX);
    if (!res) return [];
    if (!res.ok) {
      this.options.log(`work: the Host answered ${res.status} for the work check (an older plugin?)`);
      return [];
    }
    try {
      const body = (await res.json()) as { busy?: unknown };
      if (!Array.isArray(body.busy)) return [];
      return body.busy.filter((item): item is WorkItem => {
        if (!item || typeof item !== "object") return false;
        const kind = (item as WorkItem).kind;
        return typeof kind === "string" && KINDS.has(kind) && typeof (item as WorkItem).label === "string";
      });
    } catch (exc) {
      this.options.log(`work: the Host's work check did not parse: ${message(exc)}`);
      return [];
    }
  }

  /** Open or close the gate: closed, the plugin refuses new tasks and stops turns that open all the same. */
  async setGate(open: boolean): Promise<void> {
    await this.request(`${WORK_PREFIX}/gate`, { method: "POST", body: JSON.stringify({ open }) });
  }

  /** End the work in progress (the exit was confirmed); the Host answers once the work has ended. */
  async stopAll(): Promise<void> {
    await this.request(`${WORK_PREFIX}/stop`, { method: "POST" });
  }
}

function message(exc: unknown): string {
  return String((exc as Error).message ?? exc);
}
