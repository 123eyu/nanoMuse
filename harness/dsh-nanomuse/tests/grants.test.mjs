// The Permissions page's standing grants (src/desk.ts): every remembered permission as one
// list — the remote-control switch, the devices allowed without asking, the hands' per-app
// grants — with the tier each belongs to, and the grouping by tier the page draws.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { DEVICE_GRANT_PREFIX, GRANT_TIERS, grantCovers, groupGrants, REMOTE_CONTROL_GRANT_ID, standingGrants, withGrant } from '../lib/desk.js'

test('the three tiers, highest first, are the contract', () => {
  assert.deepEqual([...GRANT_TIERS], ['highest', 'confirm', 'notice'])
})

test('nothing remembered is an empty list and no groups', () => {
  assert.deepEqual(standingGrants({}), [])
  assert.deepEqual(standingGrants({ grants: [], trusted: [], remoteControl: false }), [])
  assert.deepEqual(groupGrants([]), [])
})

test('every store lands in the list with its kind, tier and a revocable id', () => {
  const list = standingGrants({
    grants: [{ id: 'g-1a2b', target: 'computer_app:Safari', at: 3000 }],
    trusted: [{ id: 'dev-9', name: 'Pixel', at: 2000 }, { id: 'dev-nameless', name: '', at: 1000 }],
    remoteControl: true,
  })
  assert.deepEqual(list, [
    { id: REMOTE_CONTROL_GRANT_ID, kind: 'remote_control', tier: 'highest', target: '', at: 0 },
    { id: `${DEVICE_GRANT_PREFIX}dev-9`, kind: 'device', tier: 'confirm', target: 'Pixel', at: 2000 },
    { id: `${DEVICE_GRANT_PREFIX}dev-nameless`, kind: 'device', tier: 'confirm', target: 'dev-nameless', at: 1000 },
    { id: 'g-1a2b', kind: 'computer_app', tier: 'confirm', target: 'Safari', at: 3000 },
  ])
  // the hands' grant keeps the id the host gave it, so the Computer page's Revoke and this one are the same call
  assert.equal(list[3].id, 'g-1a2b')
})

test('the switch off and no devices leaves the hands alone in the card tier', () => {
  const list = standingGrants({ grants: [{ id: 'g-1', target: 'computer_app:Finder', at: 1 }], remoteControl: false })
  assert.equal(list.length, 1)
  assert.equal(list[0].tier, 'confirm')
  assert.equal(list[0].target, 'Finder')
})

test('groups come in tier order, newest first inside, empty tiers left out', () => {
  const groups = groupGrants([
    { id: 'g-old', kind: 'computer_app', tier: 'confirm', target: 'Safari', at: 100 },
    { id: 'device:x', kind: 'device', tier: 'confirm', target: 'Pixel', at: 300 },
    { id: 'remote-control', kind: 'remote_control', tier: 'highest', target: '', at: 0 },
    { id: 'g-new', kind: 'computer_app', tier: 'confirm', target: 'Mail', at: 200 },
  ])
  assert.deepEqual(groups.map((g) => g.tier), ['highest', 'confirm'])
  assert.deepEqual(groups[0].grants.map((g) => g.id), ['remote-control'])
  assert.deepEqual(groups[1].grants.map((g) => g.id), ['device:x', 'g-new', 'g-old'])
  // only the card tier: one group
  assert.deepEqual(groupGrants(standingGrants({ grants: [{ id: 'g-1', target: 'computer_app:Safari', at: 1 }] })).map((g) => g.tier), ['confirm'])
})

test('a grant in one conversation covers that conversation only; one for every conversation covers all', () => {
  const grants = [
    { id: 'g-chat', target: 'computer_app:WeChat', at: 1, sessionId: 's-morning', sessionTitle: 'Morning message' },
    { id: 'g-all', target: 'computer_app:Safari', at: 2 },
  ]
  assert.equal(grantCovers(grants, 'WeChat', 's-morning'), true)
  assert.equal(grantCovers(grants, 'WeChat', 's-other'), false)
  assert.equal(grantCovers(grants, 'Safari', 's-other'), true)
  assert.equal(grantCovers(grants, 'Mail', 's-morning'), false)
  // no app in front: nothing is covered
  assert.equal(grantCovers(grants, '', 's-morning'), false)
})

test('giving the same grant again replaces it; the conversation and everywhere are kept apart', () => {
  let grants = withGrant([], { id: 'g-1', target: 'computer_app:WeChat', at: 1, sessionId: 's1', sessionTitle: 'A' })
  grants = withGrant(grants, { id: 'g-2', target: 'computer_app:WeChat', at: 2, sessionId: 's1', sessionTitle: 'A' })
  assert.deepEqual(grants.map((g) => g.id), ['g-2'])
  grants = withGrant(grants, { id: 'g-3', target: 'computer_app:WeChat', at: 3 })
  grants = withGrant(grants, { id: 'g-4', target: 'computer_app:WeChat', at: 4, sessionId: 's2', sessionTitle: 'B' })
  assert.deepEqual(grants.map((g) => g.id), ['g-2', 'g-3', 'g-4'])
  // the newest 64 are kept
  for (let i = 0; i < 70; i++) grants = withGrant(grants, { id: `g-x${i}`, target: `computer_app:App${i}`, at: 10 + i })
  assert.equal(grants.length, 64)
  assert.equal(grants.at(-1).id, 'g-x69')
})

test('a grant limited to a conversation carries it into the list', () => {
  const list = standingGrants({ grants: [{ id: 'g-c', target: 'computer_app:WeChat', at: 5, sessionId: 's1', sessionTitle: 'Morning message' }, { id: 'g-a', target: 'computer_app:Mail', at: 6 }] })
  assert.deepEqual(list, [
    { id: 'g-c', kind: 'computer_app', tier: 'confirm', target: 'WeChat', at: 5, sessionId: 's1', sessionTitle: 'Morning message' },
    { id: 'g-a', kind: 'computer_app', tier: 'confirm', target: 'Mail', at: 6 },
  ])
})
