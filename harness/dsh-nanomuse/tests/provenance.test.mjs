// Where a call sits (src/provenance.ts, relay 0.31 "Provenance"): the run, the turn and the
// step follow the session's own events, a hub task pins its run until the turn ends, the
// hands' run and step come along when the screen is being operated, and the loopback door
// finds the call a request belongs to by what the hook announced just before.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { EXPECT_TTL_MS, Provenance, lastText, lastTextOfWire } from '../lib/provenance.js'

const user = (text, kind = 'user') => ({ type: 'user/message', data: { source: { kind }, content: [{ type: 'text', text }] } })

test('the headers of a call follow the session: a person\'s turn, its number, the step; the device and the conversation beside', () => {
  const p = new Provenance()
  assert.deepEqual(p.headers(undefined, 'pc-1'), { 'x-nanomuse-device': 'pc-1', 'x-nanomuse-platform': 'desktop' })
  p.observe('s1', user('hello'))
  p.observe('s1', { type: 'turn/start', data: { turn: 3 } })
  p.observe('s1', { type: 'step/start', data: { turn: 3, step: 2 } })
  assert.deepEqual(p.headers('s1', 'pc-1'), {
    'x-nanomuse-device': 'pc-1',
    'x-nanomuse-conversation': 's1',
    'x-nanomuse-platform': 'desktop',
    'x-nanomuse-run': 'person',
    'x-nanomuse-turn': '3',
    'x-nanomuse-step': '2',
  })
  assert.deepEqual(p.fields('s1'), { platform: 'desktop', run: 'person', turn: 3, step: 2 })
  // a new turn starts the steps over
  p.observe('s1', { type: 'turn/start', data: { turn: 4 } })
  assert.deepEqual(p.fields('s1'), { platform: 'desktop', run: 'person', turn: 4 })
  // a side call of the harness for a session not seen yet says who started it, not which turn
  assert.deepEqual(p.fields('s9', 'title'), { platform: 'desktop', run: 'person' })
  assert.deepEqual(p.fields('s9'), { platform: 'desktop' })
})

test('the run follows the message that opened the turn: a schedule, a goal, another device; context notes change nothing', () => {
  const p = new Provenance()
  p.observe('s1', user('tidy up', 'schedule'))
  assert.equal(p.fields('s1').run, 'schedule')
  p.observe('s1', user('a note', 'harness-context'))
  assert.equal(p.fields('s1').run, 'schedule')
  p.observe('s1', user('round two', 'goal'))
  assert.equal(p.fields('s1').run, 'goal')
  p.observe('s1', user('from the phone', 'agent-message'))
  assert.equal(p.fields('s1').run, 'hub')
  p.observe('s1', user('me again'))
  assert.equal(p.fields('s1').run, 'person')
  assert.equal(Provenance.runOf('user'), 'person')
  assert.equal(Provenance.runOf('something-else'), undefined)
})

test('a hub task pins its run: the message the runner sends as a person\'s does not reset it, the turn\'s end does', () => {
  const p = new Provenance()
  p.setRun('s1', 'hub')
  p.observe('s1', user('do this for the phone'))
  p.observe('s1', { type: 'turn/start', data: { turn: 1 } })
  assert.equal(p.fields('s1').run, 'hub')
  p.observe('s1', { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })
  assert.equal(p.fields('s1').run, 'person')
  p.observe('s1', user('and now me'))
  assert.equal(p.fields('s1').run, 'person')
})

test('the hands\' run and step come along while the screen is being operated, and an id the relay would refuse stays home', () => {
  const p = new Provenance()
  let hands = { id: 'run-2.m2abc', step: 5 }
  p.hands(() => hands)
  p.observe('s1', { type: 'turn/start', data: { turn: 1 } })
  assert.deepEqual(p.fields('s1'), { platform: 'desktop', run: 'person', turn: 1, trajectory: 'run-2.m2abc', trajectory_step: 5 })
  assert.equal(p.headers('s1', 'pc-1')['x-nanomuse-trajectory-step'], '5')
  hands = { id: 'run 2 (bad)', step: 1 }
  assert.equal(p.fields('s1').trajectory, undefined)
  hands = { id: 'run-3.m2abd', step: 0 }
  assert.deepEqual(p.fields('s1').trajectory_step, undefined)
  hands = undefined
  assert.equal(p.fields('s1').trajectory, undefined)
})

test('the door claims the one announced call, or the one whose model and last message agree; a request nothing matches gets nothing', () => {
  let now = 1_000
  const p = new Provenance(() => now)
  p.observe('s1', { type: 'turn/start', data: { turn: 2 } })
  p.observe('s2', { type: 'turn/start', data: { turn: 7 } })
  const options = (sessionId, text) => ({ sessionId, model: 'nanomuse-chat', messages: [{ role: 'user', content: [{ type: 'text', text }] }] })
  const wire = (text) => ({ model: 'nanomuse-chat', messages: [{ role: 'user', content: text }] })

  // one waiting: claimed at once, even when the body is not readable
  const release1 = p.expect(options('s1', 'first'), 'pc-1')
  assert.equal(p.waiting, 1)
  const claimed = p.claim(undefined)
  assert.equal(claimed.sessionId, 's1')
  assert.equal(claimed.headers['x-nanomuse-turn'], '2')
  assert.equal(claimed.headers['x-nanomuse-conversation'], 's1')
  assert.equal(p.waiting, 0)
  release1()

  // two waiting: told apart by the last message's text, whitespace folded
  const release2 = p.expect(options('s1', 'the  first\nquestion'), 'pc-1')
  const release3 = p.expect(options('s2', 'the second question'), 'pc-1')
  assert.equal(p.claim(wire('the second question')).sessionId, 's2')
  assert.equal(p.claim(wire('the first question')).sessionId, 's1')
  assert.equal(p.claim(wire('the first question')), undefined)
  release2()
  release3()

  // two waiting, neither matching: nothing is guessed
  const release4 = p.expect(options('s1', 'a'), 'pc-1')
  const release5 = p.expect(options('s2', 'b'), 'pc-1')
  assert.equal(p.claim(wire('c')), undefined)
  assert.equal(p.waiting, 2)
  // the hook's disposer withdraws an announced call the stream ended without a request
  release4()
  assert.equal(p.waiting, 1)
  // a call nobody claimed in a minute is forgotten
  now += EXPECT_TTL_MS + 1
  assert.equal(p.claim(wire('b')), undefined)
  assert.equal(p.waiting, 0)
  release5()

  // one waiting but a different model: not this one
  const release6 = p.expect(options('s1', 'x'), 'pc-1')
  assert.equal(p.claim({ model: 'nanomuse-hands', messages: [] }), undefined)
  assert.equal(p.claim({ model: 'nanomuse-chat', messages: [] }).sessionId, 's1')
  release6()
})

test('the record\'s id of the latest call is kept per session, with where it was kept', () => {
  const p = new Provenance()
  assert.equal(p.lastTurn('s1'), undefined)
  p.noteTurn('s1', 'req-abc', 'cloud')
  assert.deepEqual(p.lastTurn('s1'), { id: 'req-abc', source: 'cloud' })
  p.noteTurn('s1', 'dsh-1-ff', 'own')
  assert.deepEqual(p.lastTurn('s1'), { id: 'dsh-1-ff', source: 'own' })
  p.noteTurn('s1', '', 'own')
  assert.deepEqual(p.lastTurn('s1'), { id: 'dsh-1-ff', source: 'own' })
  p.observe('s1', { type: 'turn/start', data: { turn: 12 } })
  assert.equal(p.turnOf('s1'), 12)
  p.forget('s1')
  assert.equal(p.lastTurn('s1'), undefined)
  assert.equal(p.turnOf('s1'), 0)
})

test('the last message\'s text, as the hook and as the wire see it: strings and text parts, folded and cut at 200', () => {
  assert.equal(lastText([]), '')
  assert.equal(lastText([{ role: 'user', content: 'a  b\n c' }]), 'a b c')
  assert.equal(lastText([{ role: 'user', content: [{ type: 'image', data: 'x' }, { type: 'text', text: 'see ' }, { type: 'text', text: 'this' }] }]), 'see this')
  assert.equal(lastText([{ role: 'user' }]), '')
  assert.equal(lastTextOfWire([{ role: 'user', content: [{ type: 'image_url', image_url: { url: 'x' } }, { type: 'text', text: 'see this' }] }]), 'see this')
  assert.equal(lastTextOfWire([{ role: 'user', content: 7 }]), '')
  assert.equal(lastText([{ role: 'user', content: 'x'.repeat(300) }]).length, 200)
})
