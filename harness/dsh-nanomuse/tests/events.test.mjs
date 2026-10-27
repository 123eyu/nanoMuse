// Client events (src/events.ts, relay 0.31 `POST /v1/events`): notes are codes and numbers
// only, they go up in batches a few seconds after the first, a relay that is away keeps
// them for later, a 429 pauses a minute, a 401 or 404 drops the batch, and signing out
// empties the queue.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { BATCH, ClientEvents, FLUSH_MS, QUEUE_MAX } from '../lib/events.js'

/** A fake relay and a hand-driven clock. */
function world({ enabled = true } = {}) {
  const w = { posted: [], fail: undefined, timers: [], now: 1_760_000_000_000, logs: [] }
  w.events = new ClientEvents({
    enabled: () => enabled,
    post: async (events) => {
      if (w.fail) {
        const error = new Error(`relay said ${w.fail}`)
        error.status = w.fail
        throw error
      }
      w.posted.push(events)
    },
    log: (level, text) => w.logs.push(`${level}: ${text}`),
    now: () => w.now,
    schedule: (fn, ms) => {
      w.timers.push({ fn, ms })
      return w.timers.length
    },
  })
  w.fire = async () => {
    const due = w.timers.splice(0)
    for (const t of due) t.fn()
    await new Promise((r) => setImmediate(r))
  }
  return w
}

test('a note keeps codes, numbers, booleans and null; prose and long strings stay home; the turn and the conversation ride along', async () => {
  const w = world()
  w.events.add(
    'sentinel.decision',
    { tool: 'bash', risk: 'medium', decision: 'allow', by: 'policy', ok: true, waited_s: 1.5, nothing: null, summary: 'Remove the build folder, please.', long: 'x'.repeat(130), deep: { a: 1 }, nan: Number.NaN },
    { turn: { id: 'req-1', source: 'cloud' }, conversation: 'sess-1' },
  )
  w.events.add('turn.feedback', { vote: 'up' }, { turn: { id: 'dsh-1-ab', source: 'own' } })
  w.events.add('turn.stopped', { reason: 'slow' })
  assert.equal(w.events.pending, 3)
  assert.equal(w.timers.length, 1, 'one timer for the batch')
  assert.equal(w.timers[0].ms, FLUSH_MS)
  await w.fire()
  assert.equal(w.posted.length, 1)
  const [decision, feedback, stopped] = w.posted[0]
  assert.deepEqual(decision, {
    kind: 'sentinel.decision',
    ts: 1_760_000_000,
    data: { tool: 'bash', risk: 'medium', decision: 'allow', by: 'policy', ok: true, waited_s: 1.5, nothing: null },
    turn: 'req-1',
    conversation: 'sess-1',
  })
  assert.deepEqual(feedback, { kind: 'turn.feedback', ts: 1_760_000_000, data: { vote: 'up' }, turn: 'dsh-1-ab', source: 'own' })
  assert.deepEqual(stopped, { kind: 'turn.stopped', ts: 1_760_000_000, data: { reason: 'slow' } })
  assert.equal(w.events.pending, 0)
  assert.equal(w.events.sent, 3)
})

test('notes go up in batches of at most BATCH; the queue is bounded, the oldest going first', async () => {
  const w = world()
  for (let i = 0; i < QUEUE_MAX + 5; i += 1) w.events.add('hands.step', { step: i })
  assert.equal(w.events.pending, QUEUE_MAX)
  assert.equal(w.events.dropped, 5)
  await w.fire()
  assert.equal(w.posted.length, QUEUE_MAX / BATCH)
  assert.ok(w.posted.every((batch) => batch.length === BATCH))
  assert.equal(w.posted[0][0].data.step, 5, 'the five oldest are gone')
  assert.equal(w.events.sent, QUEUE_MAX)
})

test('a relay that is away keeps the notes for later; a 429 pauses a minute; a 401 or 404 drops the batch', async () => {
  const w = world()
  w.events.add('turn.stopped', { reason: 'done' })
  w.fail = 503
  await w.fire()
  assert.equal(w.events.pending, 1, 'kept')
  assert.equal(w.timers.length, 1, 'armed again')
  // not yet: the pause is on
  await w.fire()
  assert.equal(w.posted.length, 0)
  assert.equal(w.timers.length, 1)
  w.now += 15_001
  w.fail = 429
  await w.fire()
  assert.equal(w.events.pending, 1)
  w.now += 30_000
  w.fail = undefined
  await w.fire()
  assert.equal(w.posted.length, 0, 'the minute is not over')
  w.now += 30_001
  await w.fire()
  assert.equal(w.posted.length, 1)
  assert.equal(w.events.pending, 0)

  w.events.add('turn.stopped', { reason: 'other' })
  w.fail = 401
  await w.fire()
  assert.equal(w.events.pending, 0, 'dropped')
  assert.equal(w.events.dropped, 1)
  assert.ok(w.logs.some((l) => l.includes('401')))
})

test('nothing is queued without an account, and signing out empties the queue', async () => {
  const off = world({ enabled: false })
  off.events.add('turn.stopped', { reason: 'done' })
  assert.equal(off.events.pending, 0)
  assert.equal(off.timers.length, 0)

  const w = world()
  w.events.add('turn.stopped', { reason: 'done' })
  w.events.forget()
  assert.equal(w.events.pending, 0)
  await w.fire()
  assert.equal(w.posted.length, 0)
})
