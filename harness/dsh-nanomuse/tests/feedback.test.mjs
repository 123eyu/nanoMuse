// The lightest feedback (src/client/Feedback.tsx, relay 0.31): the thumbs beside a reply and
// the question after a Stop, rendered to markup in both languages with the host's live state
// and the window's current session stubbed; the turn a reply closed, counted from the flow;
// what goes to the host's `/feedback` route. Only while an account is signed in.
import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const here = fileURLToPath(new URL('.', import.meta.url))
let dir
let bundle

/** The host's live state and the window, as the test sets them. */
const STUBS = `
export const state = { live: { cloud: { signedIn: true }, stopped: [] }, win: { current: 's1' } }
export function useLive() { return state.live }
export function useWin() { return state.win }
`

const ENTRY = `
import { createElement as h } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
export { en, zh, fill } from './src/client/locales.ts'
export { state } from 'stub:live'
import { StopAsk, makeThumbs, sendFeedback, turnNoOf, DOWN_REASONS, STOP_REASONS, STOP_ASK_MS } from './src/client/Feedback.tsx'
export { sendFeedback, turnNoOf, DOWN_REASONS, STOP_REASONS, STOP_ASK_MS }
export function renderThumbs(t) { return renderToStaticMarkup(h(makeThumbs(t))) }
export function renderStopAsk(t, sessionId) { return renderToStaticMarkup(h(StopAsk, { t, sessionId })) }
`

before(async () => {
  dir = await mkdtemp(join(tmpdir(), 'nm-feedback-'))
  await build({
    stdin: { contents: ENTRY, resolveDir: join(here, '..'), loader: 'js', sourcefile: 'feedback-entry.js' },
    outfile: join(dir, 'feedback.mjs'),
    format: 'esm',
    platform: 'node',
    target: 'node22',
    bundle: true,
    jsx: 'automatic',
    logLevel: 'silent',
    define: { 'process.env.NODE_ENV': '"production"' },
    banner: { js: "import { createRequire as __nmCreateRequire } from 'node:module'; const require = __nmCreateRequire(import.meta.url);" },
    plugins: [{
      name: 'stub-stores',
      setup(b) {
        b.onResolve({ filter: /^stub:live$/ }, () => ({ path: 'live', namespace: 'stub' }))
        b.onResolve({ filter: /\.\/(live|win)\.ts$/ }, (args) => (args.importer.endsWith('Feedback.tsx') ? { path: 'live', namespace: 'stub' } : undefined))
        b.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({ contents: STUBS, loader: 'js' }))
      },
    }],
  })
  bundle = await import(pathToFileURL(join(dir, 'feedback.mjs')).href)
})

after(async () => {
  if (dir) await rm(dir, { recursive: true, force: true })
})

function translator(lang) {
  const dict = bundle[lang]
  return (key, values) => {
    const text = dict[key]
    assert.ok(typeof text === 'string', `${lang}: no string for ${key}`)
    return values ? bundle.fill(text, values) : text
  }
}

test('the thumbs beside a reply: two buttons with their words, in both languages; nothing while signed out or outside a chat', () => {
  for (const lang of ['en', 'zh']) {
    const html = bundle.renderThumbs(translator(lang))
    assert.match(html, /data-testid="nm-thumbs"/)
    assert.equal((html.match(/class="nm-thumb"/g) ?? []).length, 2)
    assert.ok(html.includes(`aria-label="${bundle[lang].fbUp}"`), `${lang}: thumb up`)
    assert.ok(html.includes(`aria-label="${bundle[lang].fbDown}"`), `${lang}: thumb down`)
    assert.ok(!html.includes(bundle[lang].fbWhy), 'no reasons before a thumb down')
  }
  bundle.state.live = { cloud: { signedIn: false }, stopped: [] }
  assert.equal(bundle.renderThumbs(translator('en')), '')
  bundle.state.live = { cloud: { signedIn: true }, stopped: [] }
  bundle.state.win = { current: null }
  assert.equal(bundle.renderThumbs(translator('en')), '')
  bundle.state.win = { current: 's1' }
})

test('the question after a Stop: the five reasons and Skip for the stopped session, nothing for another, nothing once it is old', () => {
  const now = Date.now()
  bundle.state.live = { cloud: { signedIn: true }, stopped: [{ session: 's1', turn: 3, at: now }] }
  for (const lang of ['en', 'zh']) {
    const html = bundle.renderStopAsk(translator(lang), 's1')
    assert.match(html, /data-testid="nm-stopask"/)
    assert.ok(html.includes(bundle[lang].stopWhy))
    for (const reason of bundle.STOP_REASONS) assert.ok(html.includes(bundle[lang][`stopReason_${reason}`]), `${lang}: ${reason}`)
    assert.ok(html.includes(bundle[lang].fbSkip))
    assert.equal((html.match(/class="nm-chip nm-fb-chip"/g) ?? []).length, 5)
  }
  assert.equal(bundle.renderStopAsk(translator('en'), 's2'), '')
  assert.equal(bundle.renderStopAsk(translator('en'), undefined), '')
  bundle.state.live = { cloud: { signedIn: true }, stopped: [{ session: 's1', turn: 3, at: now - bundle.STOP_ASK_MS - 1 }] }
  assert.equal(bundle.renderStopAsk(translator('en'), 's1'), '')
  bundle.state.live = { cloud: { signedIn: true }, stopped: [] }
})

test('the turn a reply closed is the count of the person\'s messages above its tail; 0 when the flow cannot be read', () => {
  const flow = (kinds) => {
    const nodes = kinds.map((kind) => ({ kind, previousElementSibling: null, getAttribute: (name) => (name === 'data-chat-flow-kind' ? kind : null) }))
    for (let i = 1; i < nodes.length; i += 1) nodes[i].previousElementSibling = nodes[i - 1]
    const tail = nodes[nodes.length - 1]
    return { closest: (sel) => (sel === '[data-chat-flow-kind]' ? tail : null) }
  }
  assert.equal(bundle.turnNoOf(flow(['context', 'user', 'assistant', 'turn-tail'])), 1)
  assert.equal(bundle.turnNoOf(flow(['user', 'assistant', 'turn-tail', 'user', 'assistant-tool', 'assistant', 'turn-tail'])), 2)
  assert.equal(bundle.turnNoOf({ closest: () => null }), 0)
})

test('what goes to the host: the session, the turn when counted, a vote with its reason, or a stop reason; a failed post is swallowed', async () => {
  const sent = []
  const send = async (body) => { sent.push(body) }
  bundle.sendFeedback({ session: 's1', turn_no: 2, vote: 'down', reason: 'wrong' }, send)
  bundle.sendFeedback({ session: 's1', vote: '' }, send)
  bundle.sendFeedback({ session: 's1', turn_no: 3, stop_reason: 'skipped' }, send)
  bundle.sendFeedback({ session: 's1', vote: 'up' }, async () => { throw new Error('offline') })
  await new Promise((r) => setImmediate(r))
  assert.deepEqual(sent, [
    { session: 's1', turn_no: 2, vote: 'down', reason: 'wrong' },
    { session: 's1', vote: '' },
    { session: 's1', turn_no: 3, stop_reason: 'skipped' },
  ])
  assert.deepEqual([...bundle.DOWN_REASONS], ['wrong', 'unhelpful', 'unsafe', 'slow', 'other'])
})
