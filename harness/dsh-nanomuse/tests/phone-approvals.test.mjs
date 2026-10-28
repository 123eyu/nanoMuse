// A scheduled run's approval card on the phones (src/phone-approvals.ts): pushed once to "*" as
// an `approval` event for any device, a phone's `approve` read against what the card offered,
// and one `approval_result` naming who answered once the desk lets the card go.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { phoneAnswer, PhoneApprovals } from '../lib/phone-approvals.js'

const card = (id = 'ap-1', scopes = ['once', 'schedule']) => ({
  approval_id: id, preview: 'Press Enter in WeChat', preview_zh: '', reason: 'send the report', app: 'WeChat',
  task: { id: 'sch-1', title: 'Daily report' }, chat: 'Work', scopes, timeout: 600, device: 'Desk',
})

/** A hub that records pushes; `takes` false is a relay without `event_push`. */
function fakeHub(takes = true) {
  const pushed = []
  return { pushed, push: (to, body) => (takes ? (pushed.push({ to, body }), true) : false) }
}

const SELF = { id: 'pc-1', name: 'Desk' }

test('a card is pushed once to every device that can answer it, marked for any device', () => {
  const hub = fakeHub()
  const phones = new PhoneApprovals(hub, () => SELF)
  assert.equal(phones.ask(card()), true)
  assert.equal(phones.ask(card()), false, 'not twice')
  assert.deepEqual(hub.pushed, [{ to: '*', body: { stage: 'approval', ...card(), any_device: true } }])
  assert.equal(phones.has('ap-1'), true)
})

test('a relay without event_push hears nothing and nothing is kept', () => {
  const phones = new PhoneApprovals(fakeHub(false), () => SELF)
  assert.equal(phones.ask(card()), false)
  assert.equal(phones.has('ap-1'), false)
  assert.equal(phones.answer({ approval_id: 'ap-1', allow: true }), undefined)
})

test('an answer counts only when the card offered it; deny is always offered', () => {
  assert.deepEqual(phoneAnswer({ allow: true }, ['once']), { allow: true, scope: 'once' })
  assert.deepEqual(phoneAnswer({ allow: true, scope: 'schedule' }, ['once', 'schedule']), { allow: true, scope: 'schedule' })
  assert.equal(phoneAnswer({ allow: true, scope: 'schedule' }, ['once']), undefined)
  assert.equal(phoneAnswer({ allow: true, scope: 'always' }, ['once', 'schedule']), undefined)
  assert.deepEqual(phoneAnswer({ allow: false, scope: 'whatever' }, ['once']), { allow: false, scope: 'once' })
})

test('the result names the phone that answered, or this computer, and none when cancelled', () => {
  const hub = fakeHub()
  const phones = new PhoneApprovals(hub, () => SELF)
  phones.ask(card('ap-1'))
  assert.deepEqual(phones.answer({ approval_id: 'ap-1', allow: true, scope: 'schedule' }), { allow: true, scope: 'schedule' })
  phones.answeredBy('ap-1', { id: 'phone-1', name: 'Pixel', kind: 'phone' })
  assert.equal(phones.answer({ approval_id: 'ap-1', allow: false }), undefined, 'answered already')
  phones.close('ap-1', 'approved')
  assert.deepEqual(hub.pushed.at(-1), { to: '*', body: { stage: 'approval_result', approval_id: 'ap-1', status: 'approved', by: { id: 'phone-1', name: 'Pixel' } } })
  assert.equal(phones.has('ap-1'), false)

  phones.ask(card('ap-2'))
  phones.close('ap-2', 'denied')
  assert.deepEqual(hub.pushed.at(-1).body, { stage: 'approval_result', approval_id: 'ap-2', status: 'denied', by: SELF })

  phones.ask(card('ap-3'))
  phones.close('ap-3', 'cancelled')
  assert.deepEqual(hub.pushed.at(-1).body, { stage: 'approval_result', approval_id: 'ap-3', status: 'cancelled' })

  const before = hub.pushed.length
  phones.close('ap-3', 'cancelled')
  phones.close('never-asked', 'approved')
  assert.equal(hub.pushed.length, before, 'a card not out closes nothing')
})
