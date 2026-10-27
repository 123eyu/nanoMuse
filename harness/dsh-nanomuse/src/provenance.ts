/**
 * Where a model call sits (relay 0.31, docs/cloud.md "Provenance"): who started the turn
 * (`run`: a person's message, a scheduled task, a goal's round, another device through the
 * hub), the turn's number in the conversation, the call's number within the turn, and the
 * hands run and step when the screen is being operated. The session's own events say all
 * of it (`turn/start`, `step/start`, `user/message` with its `source`), so this keeps one
 * small row per session and answers two questions: the `X-NanoMuse-*` headers of a call to
 * the relay, and the same fields for a turn sent up from a key of the person's own.
 *
 * The harness's model adapter sends a provider's headers as they are, so a call to the relay
 * goes through the plugin's own loopback proxy (`relay-proxy.ts`), which has to find the
 * session a request belongs to: `expect` is called by the `llm/stream` hook just before the
 * adapter sends, `claim` by the proxy when the request arrives. One call at a time matches
 * at once; two at once are told apart by the model and the text of the last message, and a
 * request nothing matches goes out with the device and the platform only — never with a
 * guess.
 *
 * The id the relay gives a turn (`x-nanomuse-request` on its reply, or our own id for a
 * turn we sent up) is kept per session (`lastTurn`), so the notes about the turn — a thumb,
 * a stop, an approval — can name it.
 */

import type { GenerateOptions, RequestMessage, ContentBlock } from '@deepseek-ai/dsh-llm'

/** The relay's words for who started a turn. */
export type RunKind = 'person' | 'schedule' | 'feed' | 'goal' | 'hub'

/** `X-NanoMuse-Platform`: the desktop's uploads and calls go out from Node, whose user agent says nothing. */
export const PLATFORM = 'desktop'
/** A call the hook announced and no request claimed is forgotten after this long. */
export const EXPECT_TTL_MS = 60_000

/** Header names of the fields, as docs/cloud.md lists them. */
export const HEADER_OF: Record<string, string> = {
  run: 'x-nanomuse-run',
  turn: 'x-nanomuse-turn',
  step: 'x-nanomuse-step',
  trajectory: 'x-nanomuse-trajectory',
  trajectory_step: 'x-nanomuse-trajectory-step',
  platform: 'x-nanomuse-platform',
  conversation: 'x-nanomuse-conversation',
  device: 'x-nanomuse-device',
}

const ID_RE = /^[A-Za-z0-9_.:-]{1,80}$/
const DEVICE_HEADER = 'x-nanomuse-device'
const CONVERSATION_HEADER = 'x-nanomuse-conversation'

/** What one session is doing right now. */
interface SessionRow {
  run: RunKind
  /** A run set from outside the session's events (the hub's task), kept until the turn ends. */
  pinned?: RunKind
  turn: number
  step: number
  /** The record's id of the latest model call of this session, and where it was kept. */
  last?: { id: string; source: 'cloud' | 'own' }
  /** The same, by the turn the call was made in (the last call of each), for a thumb on an earlier reply. */
  byTurn: Map<number, { id: string; source: 'cloud' | 'own' }>
}

/** Turns whose ids are kept per session; older ones go. */
const TURNS_KEPT = 64

/** A call announced by the hook, waiting for the adapter's request to reach the proxy. */
interface Expected {
  sessionId: string
  model: string
  /** The last message's text, trimmed, for telling two calls apart. */
  text: string
  headers: Record<string, string>
  at: number
}

/** The hands' current run of a session, when it is being operated: its id and the step shown. */
export type TrajectoryOf = (sessionId: string) => { id: string; step: number } | undefined

export class Provenance {
  private readonly rows = new Map<string, SessionRow>()
  private readonly expected: Expected[] = []
  private trajectoryOf: TrajectoryOf = () => undefined

  constructor(private readonly now: () => number = Date.now) {}

  /** Where the hands' run and step come from (the plugin's `Trajectory`). */
  hands(fn: TrajectoryOf): void {
    this.trajectoryOf = fn
  }

  /** The relay's word for a `user/message` source kind; the harness's context notes are no run of their own. */
  static runOf(kind: string): RunKind | undefined {
    switch (kind) {
      case 'user':
        return 'person'
      case 'schedule':
        return 'schedule'
      case 'goal':
        return 'goal'
      case 'agent-message':
        return 'hub'
      default:
        return undefined
    }
  }

  /** One session event; the row follows the turn, the step and the message that opened the turn. */
  observe(sessionId: string, event: { type: string; data?: unknown }): void {
    const row = this.row(sessionId)
    const data = (event.data ?? {}) as Record<string, unknown>
    switch (event.type) {
      case 'turn/start':
        if (typeof data.turn === 'number') row.turn = data.turn
        row.step = 0
        break
      case 'step/start':
        if (typeof data.step === 'number') row.step = data.step
        if (typeof data.turn === 'number') row.turn = data.turn
        break
      case 'user/message': {
        if (row.pinned) break
        const source = data.source as { kind?: string } | undefined
        const run = Provenance.runOf(String(source?.kind ?? ''))
        if (run) row.run = run
        break
      }
      case 'turn/end':
        if (row.pinned) {
          delete row.pinned
          row.run = 'person'
        }
        break
      default:
        break
    }
  }

  /**
   * The hub asked this computer to run a turn for another device: its calls are `hub` until
   * the turn ends, whatever the message that opens it says of its source (the task runner
   * sends it as a person's).
   */
  setRun(sessionId: string, run: RunKind): void {
    const row = this.row(sessionId)
    row.run = run
    row.pinned = run
  }

  /** The row is dropped with the session. */
  forget(sessionId: string): void {
    this.rows.delete(sessionId)
  }

  /** The provenance fields of the next call of a session (`run`, `turn`, `step`, `trajectory`, `trajectory_step`, `platform`). */
  fields(sessionId: string | undefined, purpose?: string): Record<string, string | number> {
    const out: Record<string, string | number> = { platform: PLATFORM }
    if (!sessionId) return out
    const row = this.rows.get(sessionId)
    if (row) {
      out.run = row.run
      if (row.turn > 0) out.turn = row.turn
      if (row.step > 0) out.step = row.step
    } else if (purpose) {
      // a side call of the harness for a session this process has not seen a turn of yet
      out.run = 'person'
    }
    const hands = this.trajectoryOf(sessionId)
    if (hands && ID_RE.test(hands.id)) {
      out.trajectory = hands.id
      if (hands.step > 0) out.trajectory_step = hands.step
    }
    return out
  }

  /** The same, as the headers of a call to the relay (`device` and `conversation` beside them). */
  headers(sessionId: string | undefined, device: string, purpose?: string): Record<string, string> {
    const headers: Record<string, string> = {}
    if (device && ID_RE.test(device)) headers[DEVICE_HEADER] = device
    if (sessionId && ID_RE.test(sessionId)) headers[CONVERSATION_HEADER] = sessionId
    for (const [key, value] of Object.entries(this.fields(sessionId, purpose))) {
      const name = HEADER_OF[key]
      if (name) headers[name] = String(value)
    }
    return headers
  }

  // ---- the proxy's half --------------------------------------------------------------------

  /** The hook says a call is about to go out; the proxy claims it when the request arrives. */
  expect(options: GenerateOptions, device: string): () => void {
    this.sweep()
    const sessionId = options.sessionId === undefined ? '' : String(options.sessionId)
    const row: Expected = {
      sessionId,
      model: String(options.model ?? ''),
      text: lastText(options.messages ?? []),
      headers: this.headers(sessionId || undefined, device, options.purpose),
      at: this.now(),
    }
    this.expected.push(row)
    return () => {
      const i = this.expected.indexOf(row)
      if (i >= 0) this.expected.splice(i, 1)
    }
  }

  /**
   * The headers of a request the proxy received: those of the one announced call that
   * matches it (the only one waiting, or the one whose model and last message agree),
   * else nothing — the proxy adds the device and the platform itself.
   */
  claim(body: unknown): { sessionId: string; headers: Record<string, string> } | undefined {
    this.sweep()
    if (!this.expected.length) return undefined
    const b = (body && typeof body === 'object' ? body : {}) as { model?: unknown; messages?: unknown }
    const model = typeof b.model === 'string' ? b.model : ''
    const text = Array.isArray(b.messages) ? lastTextOfWire(b.messages as unknown[]) : ''
    let found: Expected | undefined
    if (this.expected.length === 1 && (!model || this.expected[0]?.model === model)) found = this.expected[0]
    else {
      const same = this.expected.filter((e) => e.model === model && e.text === text)
      // two identical calls at once cannot be told apart; the older one goes first
      found = same[0]
    }
    if (!found) return undefined
    this.expected.splice(this.expected.indexOf(found), 1)
    return { sessionId: found.sessionId, headers: found.headers }
  }

  /** How many announced calls wait for a request (the tests read it). */
  get waiting(): number {
    return this.expected.length
  }

  private sweep(): void {
    const cutoff = this.now() - EXPECT_TTL_MS
    while (this.expected.length && (this.expected[0]?.at ?? 0) < cutoff) this.expected.shift()
  }

  // ---- the record's ids ----------------------------------------------------------------------

  /** The record kept the session's latest call under `id` (`cloud`: the relay's own; `own`: the id we sent it up under). */
  noteTurn(sessionId: string, id: string, source: 'cloud' | 'own'): void {
    if (!sessionId || !id) return
    const row = this.row(sessionId)
    row.last = { id: id.slice(0, 96), source }
    if (row.turn > 0) {
      row.byTurn.set(row.turn, row.last)
      while (row.byTurn.size > TURNS_KEPT) row.byTurn.delete(row.byTurn.keys().next().value as number)
    }
  }

  /** The record's id of the session's latest call, when known. */
  lastTurn(sessionId: string): { id: string; source: 'cloud' | 'own' } | undefined {
    return this.rows.get(sessionId)?.last
  }

  /** The record's id of the last call of turn `turn` of the session, when it was seen. */
  turnAt(sessionId: string, turn: number): { id: string; source: 'cloud' | 'own' } | undefined {
    return this.rows.get(sessionId)?.byTurn.get(turn)
  }

  /** The turn number the session is on (0 before the first). */
  turnOf(sessionId: string): number {
    return this.rows.get(sessionId)?.turn ?? 0
  }

  private row(sessionId: string): SessionRow {
    let row = this.rows.get(sessionId)
    if (!row) {
      row = { run: 'person', turn: 0, step: 0, byTurn: new Map() }
      this.rows.set(sessionId, row)
    }
    return row
  }
}

/** The text of the last message of a request, as the hook sees it. */
export function lastText(messages: readonly RequestMessage[]): string {
  const last = messages[messages.length - 1]
  if (!last) return ''
  const blocks = last.content as readonly ContentBlock[] | string | undefined
  if (typeof blocks === 'string') return norm(blocks)
  if (!Array.isArray(blocks)) return ''
  let text = ''
  for (const block of blocks) if (block.type === 'text') text += block.text
  return norm(text)
}

/** The text of the last message of a request, as the wire carries it (OpenAI's shape). */
export function lastTextOfWire(messages: unknown[]): string {
  const last = messages[messages.length - 1] as { content?: unknown } | undefined
  if (!last) return ''
  const content = last.content
  if (typeof content === 'string') return norm(content)
  if (!Array.isArray(content)) return ''
  let text = ''
  for (const part of content as { type?: string; text?: string }[]) if (part?.type === 'text' && typeof part.text === 'string') text += part.text
  return norm(text)
}

/** Whitespace folded and the text cut: the adapter may trim or wrap what it sends, the start stays. */
function norm(text: string): string {
  return text.replace(/\s+/g, ' ').trim().slice(0, 200)
}
