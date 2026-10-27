/**
 * What happened around a turn, told to the account's record as codes and numbers
 * (relay 0.31, `POST /v1/events`, docs/cloud.md "Client events"): what was decided about a
 * tool call and who decided it, how an approval went and how long it waited, a run the
 * person stopped and the reason they gave, a thumb on a reply, a step of the hands. Never
 * a message, an argument, a summary or a screen — the relay drops anything that looks like
 * prose, and nothing of the kind is put in here to begin with.
 *
 * Notes are queued in memory and go up in batches of at most `BATCH` a few seconds after
 * the first of them; a relay that is away keeps them for the next round, a 429 pauses a
 * minute, a 401 or 404 (signed out meanwhile; a relay without the record) drops the batch.
 * Nothing is sent without a signed-in account, and signing out empties the queue.
 */

/** The relay takes at most this many events in one request. */
export const BATCH = 100
/** Notes made within this long of the first go up together. */
export const FLUSH_MS = 4_000
/** The queue's cap; past it the oldest note goes. */
export const QUEUE_MAX = 1_000

const CODE = /^[A-Za-z0-9_.:/ -]{0,120}$/

export interface ClientEvent {
  kind: string
  ts: number
  data: Record<string, string | number | boolean | null>
  turn?: string
  source?: 'own'
  conversation?: string
}

export interface ClientEventsOptions {
  /** Whether a note may go up right now (signed in). */
  enabled: () => boolean
  /** `POST /v1/events`; throws a `RelayError` (its `status`) when refused. */
  post: (events: ClientEvent[]) => Promise<void>
  log?: (level: 'info' | 'warn', text: string) => void
  now?: () => number
  /** `setTimeout` to use; the tests pass their own. */
  schedule?: (fn: () => void, ms: number) => unknown
}

export class ClientEvents {
  private queue: ClientEvent[] = []
  private timer: unknown
  private sending = false
  private pausedUntil = 0
  /** Notes sent since the start; the tests and the log read them. */
  sent = 0
  dropped = 0

  constructor(private readonly options: ClientEventsOptions) {}

  /**
   * One note. `data` keeps codes, numbers and booleans; a string that looks like a sentence
   * is left out here, before it travels. `turn` is the record's id of the turn it is about.
   */
  add(kind: string, data: Record<string, unknown>, where: { turn?: { id: string; source: 'cloud' | 'own' } | undefined; conversation?: string } = {}): void {
    if (!this.options.enabled()) return
    const clean: ClientEvent['data'] = {}
    for (const [key, value] of Object.entries(data)) {
      const k = key.slice(0, 32)
      if (typeof value === 'boolean' || value === null || (typeof value === 'number' && Number.isFinite(value))) clean[k] = value
      else if (typeof value === 'string') {
        // a code, whole or not at all: a cut string would mean something else
        const s = value.trim()
        if (CODE.test(s)) clean[k] = s
      }
    }
    const event: ClientEvent = { kind, ts: Math.floor((this.options.now ?? Date.now)() / 1000), data: clean }
    if (where.turn?.id) {
      event.turn = where.turn.id
      if (where.turn.source === 'own') event.source = 'own'
    }
    if (where.conversation) event.conversation = where.conversation.slice(0, 80)
    if (this.queue.length >= QUEUE_MAX) {
      this.queue.shift()
      this.dropped += 1
    }
    this.queue.push(event)
    this.arm()
  }

  /** How many notes wait. */
  get pending(): number {
    return this.queue.length
  }

  /** At sign-out: nothing queued under the account goes up later. */
  forget(): void {
    this.queue = []
  }

  private arm(): void {
    if (this.timer) return
    const schedule = this.options.schedule ?? ((fn, ms) => setTimeout(fn, ms).unref())
    this.timer = schedule(() => {
      this.timer = undefined
      void this.flush()
    }, FLUSH_MS)
  }

  /** Send what is queued, oldest first; what the relay would not take now waits. */
  async flush(): Promise<void> {
    if (this.sending || !this.queue.length || !this.options.enabled()) return
    if ((this.options.now ?? Date.now)() < this.pausedUntil) {
      this.arm()
      return
    }
    this.sending = true
    try {
      while (this.queue.length) {
        const batch = this.queue.slice(0, BATCH)
        try {
          await this.options.post(batch)
        } catch (error: unknown) {
          const status = (error as { status?: number }).status ?? 0
          if (status === 401 || status === 404 || (status >= 400 && status < 500 && status !== 429)) {
            this.queue.splice(0, batch.length)
            this.dropped += batch.length
            this.options.log?.('info', `nanomuse events: the relay said ${status}; ${batch.length} notes dropped`)
            continue
          }
          const now = (this.options.now ?? Date.now)()
          this.pausedUntil = now + (status === 429 ? 60_000 : 15_000)
          this.options.log?.('info', `nanomuse events: not sent (${error instanceof Error ? error.message : String(error)}); trying again later`)
          this.arm()
          return
        }
        this.queue.splice(0, batch.length)
        this.sent += batch.length
      }
    } finally {
      this.sending = false
    }
  }
}
