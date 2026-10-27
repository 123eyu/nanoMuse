/**
 * The lightest feedback (relay 0.31, docs/cloud.md "Client events"): a thumb up or down
 * beside each reply, with a one-word reason after a thumb down, and, right after the person
 * pressed Stop, one question above the composer — why — with five answers and Skip. Both
 * go to the host's `POST /nanomuse/cloud/feedback`, which turns them into `turn.feedback`
 * and `turn.stopped` notes for the account's record, codes only. Nothing is asked twice: a
 * thumb stays as it was set (and a second click on the same thumb takes it back), the stop
 * question goes with its answer, with Skip, or with the next turn.
 *
 * The thumbs sit in `conversation.chat.assistant-actions`, the seat the harness gives every
 * reply's tail, where our quote button already is; the turn's number is counted from the
 * person's messages above the tail (each opens a turn), so a thumb on an earlier reply names
 * its own turn; when the flow cannot be read the host takes the latest.
 */
import { createElement as h, Fragment, useEffect, useRef, useState, type ReactNode } from 'react'
import { call, type Translate } from './api.ts'
import { IconThumbDown, IconThumbUp } from './icons.tsx'
import { useLive } from './live.ts'
import { useWin } from './win.ts'

export type Vote = 'up' | 'down' | ''
export const DOWN_REASONS = ['wrong', 'unhelpful', 'unsafe', 'slow', 'other'] as const
export const STOP_REASONS = ['wrong', 'slow', 'changed_mind', 'done', 'other'] as const
/** The stop question is withdrawn on its own after this long. */
export const STOP_ASK_MS = 90_000

/** What was set on each reply, by session and turn, for the page's lifetime: a thumb is not asked again. */
const votes = new Map<string, { vote: Vote; reason: string }>()

/** The turn a reply closed: the person's messages above it in the flow, counted; 0 when the flow cannot be read. */
export function turnNoOf(from: Element): number {
  const node = from.closest('[data-chat-flow-kind]')
  if (!node) return 0
  let n = 0
  for (let el: Element | null = node; el; el = el.previousElementSibling) {
    if (el.getAttribute('data-chat-flow-kind') === 'user') n += 1
  }
  return n
}

export interface FeedbackBody {
  session: string
  turn_no?: number
  vote?: Vote
  reason?: string
  stop_reason?: string
}

/** The host's route; a failure is nobody's business but the log's. */
export function sendFeedback(body: FeedbackBody, send: (body: FeedbackBody) => Promise<unknown> = (b) => call('feedback', b)): void {
  void send(body).catch(() => undefined)
}

export function makeThumbs(t: Translate, send?: (body: FeedbackBody) => Promise<unknown>) {
  return function Thumbs(): ReactNode {
    const self = useRef<HTMLSpanElement | null>(null)
    const { current } = useWin()
    const live = useLive()
    const [turnNo, setTurnNo] = useState(0)
    const [, redraw] = useState(0)
    const [asking, setAsking] = useState(false)
    useEffect(() => {
      if (self.current) setTurnNo(turnNoOf(self.current))
    }, [])
    if (!current || !live.cloud.signedIn) return null
    const key = `${current}\n${turnNo}`
    const set = votes.get(key) ?? { vote: '' as Vote, reason: '' }
    const post = (vote: Vote, reason = '') => {
      votes.set(key, { vote, reason })
      sendFeedback({ session: current, ...(turnNo ? { turn_no: turnNo } : {}), vote, ...(reason ? { reason } : {}) }, send)
      redraw((n) => n + 1)
    }
    const thumb = (vote: 'up' | 'down') => {
      if (set.vote === vote) {
        post('')
        setAsking(false)
        return
      }
      post(vote)
      setAsking(vote === 'down')
    }
    return h('span', { ref: self, className: 'nm-thumbs', 'data-testid': 'nm-thumbs' },
      h('button', { type: 'button', className: `nm-thumb${set.vote === 'up' ? ' nm-on' : ''}`, 'aria-label': t('fbUp'), 'aria-pressed': set.vote === 'up', title: t('fbUp'), onClick: () => thumb('up') }, h(IconThumbUp, { size: 14, filled: set.vote === 'up' })),
      h('button', { type: 'button', className: `nm-thumb${set.vote === 'down' ? ' nm-on' : ''}`, 'aria-label': t('fbDown'), 'aria-pressed': set.vote === 'down', title: t('fbDown'), onClick: () => thumb('down') }, h(IconThumbDown, { size: 14, filled: set.vote === 'down' })),
      asking && set.vote === 'down' && !set.reason
        ? h('span', { className: 'nm-fb-reasons', role: 'group', 'aria-label': t('fbWhy') },
            h('span', { className: 'nm-fb-why' }, t('fbWhy')),
            ...DOWN_REASONS.map((reason) => h('button', { key: reason, type: 'button', className: 'nm-chip nm-fb-chip', onClick: () => { post('down', reason); setAsking(false) } }, t(`fbReason_${reason}`))),
            h('button', { type: 'button', className: 'nm-ob-link nm-inline', onClick: () => setAsking(false) }, t('fbSkip')))
        : set.vote === 'down' && set.reason
          ? h('span', { className: 'nm-fb-thanks' }, t('fbThanks'))
          : null)
  }
}

export interface StopAskProps {
  t: Translate
  sessionId?: string | undefined
  send?: ((body: FeedbackBody) => Promise<unknown>) | undefined
}

/** The question above the composer after a Stop: one line, five answers, Skip. */
export function StopAsk({ t, sessionId, send }: StopAskProps): ReactNode {
  const live = useLive()
  const stop = (live.stopped ?? []).find((s) => s.session === sessionId)
  const [gone, setGone] = useState<string | null>(null)
  const [, tick] = useState(0)
  useEffect(() => {
    if (!stop) return undefined
    const left = STOP_ASK_MS - (Date.now() - stop.at)
    if (left <= 0) return undefined
    const timer = window.setTimeout(() => tick((n) => n + 1), left + 50)
    return () => window.clearTimeout(timer)
  }, [stop?.at])
  if (!stop || !sessionId) return null
  const key = `${sessionId}\n${stop.turn}`
  if (gone === key || Date.now() - stop.at >= STOP_ASK_MS) return null
  const answer = (reason: string) => {
    setGone(key)
    sendFeedback({ session: sessionId, turn_no: stop.turn, stop_reason: reason }, send)
  }
  return h('div', { className: 'nm-stopask', role: 'group', 'aria-label': t('stopWhy'), 'data-testid': 'nm-stopask' },
    h('span', { className: 'nm-stopask-text' }, t('stopWhy')),
    h(Fragment, null, ...STOP_REASONS.map((reason) => h('button', { key: reason, type: 'button', className: 'nm-chip nm-fb-chip', onClick: () => answer(reason) }, t(`stopReason_${reason}`)))),
    h('button', { type: 'button', className: 'nm-ob-link nm-inline', onClick: () => answer('skipped') }, t('fbSkip')))
}
