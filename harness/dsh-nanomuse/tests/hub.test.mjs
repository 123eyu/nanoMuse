// The hub client against a fake socket: hello with the key, welcome, calls and
// their results/events/errors, incoming calls answered, the device list,
// reconnect after a drop, and a refused key.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { HubClient, HubError } from '../lib/hub.js'

const OPEN = 1

/** A socket the test drives: `sent` collects frames, `push` delivers frames, `drop` closes. */
class FakeSocket {
  constructor(url) {
    this.url = url
    this.readyState = 0
    this.sent = []
    this.listeners = { open: [], message: [], close: [], error: [] }
  }
  addEventListener(type, fn) {
    this.listeners[type].push(fn)
  }
  send(data) {
    this.sent.push(JSON.parse(data))
  }
  close(code = 1000, reason = '') {
    if (this.readyState === 3) return
    this.readyState = 3
    for (const fn of this.listeners.close) fn({ code, reason })
  }
  open() {
    this.readyState = OPEN
    for (const fn of this.listeners.open) fn({})
  }
  push(frame) {
    for (const fn of this.listeners.message) fn({ data: JSON.stringify(frame) })
  }
  drop(code = 1006, reason = '') {
    this.readyState = 3
    for (const fn of this.listeners.close) fn({ code, reason })
  }
}

const DEVICE = { id: 'pc-dsh-test', name: 'Desk', kind: 'computer', os: 'Linux', version: 'test', actions: ['info', 'notify'] }

function harness({ key = 'nm_test', pingMs = 100_000 } = {}) {
  const sockets = []
  const client = new HubClient({
    url: 'ws://relay/v1/hub',
    key: async () => key,
    device: () => DEVICE,
    socket: (url) => {
      const s = new FakeSocket(url)
      sockets.push(s)
      return s
    },
    backoffMs: { min: 5, max: 20 },
    pingMs,
  })
  return { client, sockets }
}

const tick = () => new Promise((r) => setTimeout(r, 0))
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

async function connected(h) {
  h.client.start()
  await tick()
  const s = h.sockets.at(-1)
  s.open()
  assert.deepEqual(s.sent[0], { type: 'hello', key: 'nm_test', device: DEVICE })
  s.push({ type: 'welcome', device_id: DEVICE.id, devices: [{ id: DEVICE.id, name: 'Desk', kind: 'computer', online: true }, { id: 'phone-1', name: 'Phone', kind: 'phone', online: true, controllable: true }], server: {} })
  assert.equal(h.client.connected, true)
  assert.equal(h.client.deviceId, DEVICE.id)
  return s
}

test('hello carries the key; welcome brings the device list', async () => {
  const h = harness()
  const s = await connected(h)
  assert.equal(h.client.devices.length, 2)
  assert.equal(h.client.find('phone')?.id, 'phone-1')
  assert.equal(h.client.find('PHONE')?.id, 'phone-1')
  assert.equal(h.client.find('')?.id, 'phone-1', 'the only other device')
  assert.equal(h.client.find('tablet'), undefined)
  h.client.stop()
  assert.equal(s.readyState, 3)
  assert.equal(h.client.connected, false)
})

test('a call resolves with the result body, sees events, and fails on an error frame', async () => {
  const h = harness()
  const s = await connected(h)
  const events = []
  const p = h.client.call('phone-1', 'task', { text: 'hi' }, { onEvent: (b) => events.push(b), timeoutMs: 1000 })
  const sent = s.sent.at(-1)
  assert.equal(sent.type, 'call')
  assert.equal(sent.to, 'phone-1')
  assert.equal(sent.action, 'task')
  s.push({ type: 'event', id: sent.id, body: { stage: 'tool', name: 'screen' } })
  s.push({ type: 'result', id: sent.id, ok: true, body: { text: 'done' } })
  assert.deepEqual(await p, { text: 'done' })
  assert.deepEqual(events, [{ stage: 'tool', name: 'screen' }])

  const p2 = h.client.call('phone-1', 'screen', {}, { timeoutMs: 1000 })
  const id2 = s.sent.at(-1).id
  s.push({ type: 'error', id: id2, code: 'device_offline', message: 'gone' })
  await assert.rejects(p2, (e) => e instanceof HubError && e.code === 'device_offline')

  const p3 = h.client.call('phone-1', 'shell', { command: 'ls' }, { timeoutMs: 1000 })
  const id3 = s.sent.at(-1).id
  s.push({ type: 'result', id: id3, ok: false, error: 'denied', message: 'no' })
  await assert.rejects(p3, (e) => e instanceof HubError && e.code === 'denied' && e.message === 'no')

  const p4 = h.client.call('phone-1', 'info', {}, { timeoutMs: 10 })
  await assert.rejects(p4, (e) => e instanceof HubError && e.code === 'timeout')

  // A caller may pick the id up front, so it can ask the device to `stop` that very call.
  const id5 = h.client.nextId()
  const p5 = h.client.call('phone-1', 'task', { text: 'later' }, { id: id5, timeoutMs: 1000 })
  assert.equal(s.sent.at(-1).id, id5)
  s.push({ type: 'result', id: id5, ok: true, body: { text: 'ok' } })
  assert.deepEqual(await p5, { text: 'ok' })
  h.client.stop()
})

test('an incoming call is answered by its handler; unknown actions are refused', async () => {
  const h = harness()
  const s = await connected(h)
  h.client.handle('notify', async (args, call) => {
    call.event({ stage: 'shown' })
    return { ok: true, text: args.text }
  })
  s.push({ type: 'call', id: 'c1', from: { id: 'phone-1', name: 'Phone', kind: 'phone' }, action: 'notify', args: { text: 'dinner' } })
  await tick()
  assert.deepEqual(s.sent.at(-2), { type: 'event', id: 'c1', body: { stage: 'shown' } })
  assert.deepEqual(s.sent.at(-1), { type: 'result', id: 'c1', ok: true, body: { ok: true, text: 'dinner' } })
  s.push({ type: 'call', id: 'c2', from: { id: 'phone-1', name: 'Phone', kind: 'phone' }, action: 'shell', args: {} })
  await tick()
  assert.equal(s.sent.at(-1).ok, false)
  assert.equal(s.sent.at(-1).error, 'unknown_action')
  h.client.stop()
})

test('a dropped socket reconnects; a refused key stops and reports', async () => {
  const h = harness()
  let states = 0
  let unauthorized = 0
  h.client.onState(() => states++)
  h.client.onUnauthorized(() => unauthorized++)
  const s = await connected(h)
  const pending = h.client.call('phone-1', 'info', {}, { timeoutMs: 1000 })
  s.drop(1006, 'network')
  await assert.rejects(pending, (e) => e instanceof HubError && e.code === 'disconnected')
  assert.equal(h.client.connected, false)
  await wait(30)
  assert.equal(h.sockets.length, 2, 'a new socket was opened')
  const s2 = h.sockets[1]
  s2.open()
  s2.push({ type: 'welcome', device_id: DEVICE.id, devices: [] })
  assert.equal(h.client.connected, true)
  s2.drop(4001, 'bad key')
  await tick()
  assert.equal(unauthorized, 1)
  assert.equal(h.client.connected, false)
  await wait(30)
  assert.equal(h.sockets.length, 2, 'no reconnect after a refused key')
  assert.ok(states >= 3)
})

test('without a key nothing connects until restart', async () => {
  let key
  const sockets = []
  const client = new HubClient({ url: 'ws://relay/v1/hub', key: async () => key, device: () => DEVICE, socket: (u) => { const s = new FakeSocket(u); sockets.push(s); return s }, backoffMs: { min: 5, max: 20 } })
  client.start()
  await tick()
  assert.equal(sockets.length, 0)
  key = 'nm_test'
  client.restart()
  await tick()
  assert.equal(sockets.length, 1)
  client.stop()
})

test('the sync frame carries the cursor and the device; the working frame (C9) reaches its listener as it came', async () => {
  const h = harness()
  const s = await connected(h)
  const syncs = []
  const working = []
  const offSync = h.client.onSync((cursor, from) => syncs.push([cursor, from]))
  const offWorking = h.client.onWorking((frame) => working.push(frame))
  s.push({ type: 'sync', what: 'conversations', cursor: 42, from: 'phone-1' })
  s.push({ type: 'sync', what: 'something-else', cursor: 43, from: 'phone-1' })
  s.push({ type: 'working', cid: 'c1', from: 'phone-1', device_name: 'Pixel 8', working: true, at: 1738000000 })
  s.push({ type: 'working', cid: 'c1', from: 'phone-1', device_name: 'Pixel 8', working: false, at: 1738000009 })
  s.push({ type: 'working', cid: 'c2' })
  assert.deepEqual(syncs, [[42, 'phone-1']])
  assert.deepEqual(working, [
    { cid: 'c1', from: 'phone-1', device_name: 'Pixel 8', working: true, at: 1738000000 },
    { cid: 'c1', from: 'phone-1', device_name: 'Pixel 8', working: false, at: 1738000009 },
    { cid: 'c2', from: '', device_name: '', working: false, at: 0 },
  ])
  offSync()
  offWorking()
  s.push({ type: 'working', cid: 'c3', from: 'phone-1', device_name: 'Pixel 8', working: true, at: 1 })
  assert.equal(working.length, 3)
  h.client.stop()
})

test('a restart while the key is still being read opens one socket, not two', async () => {
  // The key comes from the vault asynchronously; a sign-in's restart() landing in that
  // window used to leave the first attempt's socket open next to the new one.
  let release
  const gate = new Promise((r) => (release = r))
  const sockets = []
  const client = new HubClient({
    url: 'ws://relay/v1/hub',
    key: async () => {
      await gate
      return 'nm_test'
    },
    device: () => DEVICE,
    socket: (url) => {
      const s = new FakeSocket(url)
      sockets.push(s)
      return s
    },
    backoffMs: { min: 5, max: 20 },
    pingMs: 100_000,
  })
  client.start()
  await tick()
  client.restart()
  await tick()
  assert.equal(sockets.length, 0)
  release()
  await tick()
  await tick()
  assert.equal(sockets.length, 1, 'the attempt that lost the race opens nothing')
  client.stop()
})

test('a restart fails the calls in flight at once and is offline until the new welcome', async () => {
  const h = harness()
  const s1 = await connected(h)
  const states = []
  h.client.onState(() => states.push(h.client.connected))
  const inFlight = h.client.call('phone-1', 'info', {}, { timeoutMs: 60_000 })
  h.client.restart()
  await assert.rejects(inFlight, (e) => e instanceof HubError && e.code === 'disconnected')
  assert.equal(h.client.connected, false, 'not connected between the sockets')
  assert.deepEqual(states, [false])
  // a call made before the new socket is open is refused, not written into a closed socket
  await assert.rejects(h.client.call('phone-1', 'info', {}), (e) => e instanceof HubError && e.code === 'offline')
  await tick()
  const s2 = h.sockets.at(-1)
  assert.notEqual(s2, s1)
  assert.equal(s2.sent.length, 0, 'nothing is sent before the socket opens')
  s2.open()
  s2.push({ type: 'welcome', device_id: DEVICE.id, devices: [], server: {} })
  assert.equal(h.client.connected, true)
  assert.deepEqual(states, [false, true])
  h.client.stop()
})

test('hub_paused waits well beyond the normal back-off; a refused device stays closed without signing out', async () => {
  const h = harness()
  const s = await connected(h)
  s.drop(4003, 'hub_paused')
  assert.equal(h.client.lastError, 'the hub is paused on the relay')
  await wait(60)
  assert.equal(h.sockets.length, 1, 'no reconnect within the normal back-off')
  h.client.stop()

  const h2 = harness()
  let unauthorized = 0
  h2.client.onUnauthorized(() => (unauthorized += 1))
  const s2 = await connected(h2)
  s2.drop(4002, 'bad device')
  await wait(60)
  assert.equal(h2.sockets.length, 1, 'the same device again would be refused again')
  assert.equal(h2.client.lastError, 'the relay refused this device')
  assert.equal(unauthorized, 0, 'a refused device is not a refused key')
  h2.client.restart()
  await tick()
  assert.equal(h2.sockets.length, 2, 'a restart (a new sign-in, a rename) tries again')
  h2.client.stop()
})

test('a ping is sent on the interval; a relay that answers nothing for two pings is dropped and the client reconnects', async () => {
  const h = harness({ pingMs: 40 })
  const s = await connected(h)
  const states = []
  h.client.onState(() => states.push(h.client.connected))
  await wait(60)
  assert.equal(s.sent.filter((f) => f.type === 'ping').length, 1, 'the first ping went out')
  s.push({ type: 'pong' })
  await wait(40)
  assert.equal(s.sent.filter((f) => f.type === 'ping').length, 2, 'a pong keeps the socket; the second ping went out')
  assert.equal(h.client.connected, true)
  // nothing answers the second ping: a call made meanwhile fails when the socket is dropped
  const failed = h.client.call('phone-1', 'screen', {}, { timeoutMs: 5000 }).then(() => undefined, (e) => e)
  await wait(40)
  assert.equal(h.client.connected, false, 'two pings without a frame back: the link is dead')
  assert.equal(s.readyState, 3, 'the silent socket was closed by the client')
  assert.equal(h.client.lastError, 'the relay stopped answering')
  const e = await failed
  assert.ok(e instanceof HubError && e.code === 'disconnected', 'the call in flight failed at once')
  await wait(40)
  assert.equal(h.sockets.length, 2, 'the usual reconnect followed')
  const s2 = h.sockets.at(-1)
  s2.open()
  s2.push({ type: 'welcome', device_id: DEVICE.id, devices: [], server: {} })
  assert.equal(h.client.connected, true)
  assert.deepEqual(states, [false, true])
  h.client.stop()
})
