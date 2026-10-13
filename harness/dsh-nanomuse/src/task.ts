/**
 * A task from another device, run by this computer's Muse in a dsh session —
 * the runtime's `task` action (`docs/hub.md`), so the phone's `delegate` lands
 * here exactly as it lands on a runtime.
 *
 * One session per asking device and conversation, titled "From <device>", kept
 * for the next task from the same place. The call streams the run back as
 * `event` frames in the runtime's vocabulary — `tool`, `tool_result`, `text`
 * (interim), `approval`, `approval_result` — and resolves with the final answer.
 *
 * Approvals the run asks for go to the asker: the person is at the other device,
 * so its card shows there and its `approve {approval_id, allow}` decides; the
 * local card is not raised for these sessions. `stop {call}` cancels the run.
 */
import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { ApprovalOutcome, ApprovalRequestEvent } from '@deepseek-ai/dsh-user-approval/types'
import { createUserMessage, type ContentBlock } from '@deepseek-ai/dsh-llm'
import type { Session, SessionEvent, SessionId } from '@deepseek-ai/dsh-session'
import type {} from '@deepseek-ai/dsh-api-session-controller'
import { HubError, type Caller, type IncomingCall } from './hub.ts'

/** The runtime's TASK_TIMEOUT_S: a task may run this long before the hub gives up on it. */
export const TASK_TIMEOUT_MS = 15 * 60_000
/** How long an approval relayed to the asker may stay unanswered. */
export const APPROVAL_TIMEOUT_S = 180

type ApprovalStatus = 'approved' | 'denied' | 'expired' | 'cancelled'

interface Run {
  readonly callId: string
  readonly call: IncomingCall
  readonly sessionId: SessionId
  readonly agent: Agent
  /** The tool calls of the run so far, for approval previews and result names. */
  readonly tools: Map<string, { name: string; args: string }>
  /** Approvals waiting on the asker, cancelled with the run. */
  readonly approvals: Set<string>
  final: string
  resolve(text: string): void
  reject(error: HubError): void
}

export interface TaskRunnerOptions {
  /** This computer's name on the account, for the result and the approval cards over there. */
  deviceName(): string
  log(level: 'info' | 'warn', text: string): void
  /** A task started or ended here; the client shows it as a notice. */
  notice?(from: string, text: string): void
  /** The session a conversation lived in before (survives a restart); absent means remember in memory only. */
  recall?(key: string): string | undefined
  remember?(key: string, sessionId: string): void
  /**
   * Whether this computer is handing over (a protected exit, later a restart-update):
   * while it says true, a task is refused with the usual `busy` error: the asker sees
   * the refusal and can ask again; nothing is taken and dropped.
   */
  closed?: () => boolean
}

/** The text a task arrives as: the model learns where it came from and where it is, as the runtime's bubble says. */
export function frame(from: string, here: string, text: string): string {
  return `[Asked from ${from}, another device on this account. You are the Muse on ${here}: do it here, on ${here}, and answer briefly — the answer goes back to ${from}.]\n${text}`
}

/** Plain text out of content blocks (what the asker sees of a tool result or an answer). */
export function textOf(content: readonly ContentBlock[] | undefined): string {
  if (!content) return ''
  return content
    .map((block) => (block.type === 'text' ? block.text : ''))
    .filter(Boolean)
    .join('\n')
    .trim()
}

/** `name: the arguments on one line`, bounded, for the asker's step list and approval card. */
export function preview(name: string, args: string): string {
  let shown = args.trim()
  try {
    const parsed: unknown = JSON.parse(args)
    if (parsed && typeof parsed === 'object') {
      const o = parsed as Record<string, unknown>
      const main = o.command ?? o.cmd ?? o.path ?? o.file_path ?? o.url ?? o.query ?? o.pattern ?? o.task ?? o.prompt
      const first = Object.values(o).find((v) => typeof v === 'string')
      shown = typeof main === 'string' ? main : typeof first === 'string' ? first : Object.keys(o).length ? JSON.stringify(o) : ''
    }
  } catch {
    // not JSON (a model mid-call) — show it raw
  }
  shown = shown.replace(/\s+/g, ' ').trim()
  if (shown.length > 160) shown = `${shown.slice(0, 159)}…`
  return shown ? `${name}: ${shown}` : name
}

export class TaskRunner {
  private readonly bySession = new Map<SessionId, Run>()
  private readonly byCall = new Map<string, Run>()
  /** `<device id>\n<conversation>` → the session that conversation lives in. */
  private readonly conversations = new Map<string, SessionId>()
  private readonly pendingApprovals = new Map<string, (status: ApprovalStatus) => void>()
  private approvalSeq = 0

  constructor(
    private readonly ctx: Context,
    private readonly options: TaskRunnerOptions,
  ) {}

  /** Follow the session feed and answer approvals for the runs; returns the disposer. */
  attach(): () => void {
    const offEvents = this.ctx.on('session/event', (session: Session, event: SessionEvent) => this.onEvent(session, event))
    const offApproval = this.ctx.on('approval/request', (req: ApprovalRequestEvent, next: () => Promise<ApprovalOutcome>) => this.onApproval(req, next), true)
    return () => {
      offEvents()
      offApproval()
      for (const run of [...this.byCall.values()]) run.reject(new HubError('failed', 'this computer stopped taking tasks'))
    }
  }

  get running(): number {
    return this.byCall.size
  }

  /** The runs on, as the session each lives in and the device that asked: the exit's aggregate names the work with them. */
  origins(): { sessionId: string; from: string }[] {
    return [...this.byCall.values()].map((run) => ({ sessionId: String(run.sessionId), from: run.call.from.name || run.call.from.id }))
  }

  /** `task {text, from?, conversation?}` → `{text, conversation, thread, device}`. */
  async task(args: Record<string, unknown>, call: IncomingCall): Promise<Record<string, unknown>> {
    if (this.options.closed?.()) throw new HubError('busy', 'this computer is ending its work; ask again in a moment')
    const text = String(args.text ?? '').trim()
    if (!text) throw new HubError('usage', 'text is required')
    const from = call.from
    const conversation = String(args.conversation ?? '').trim() || from.id || 'device'
    const sessionId = await this.sessionFor(from, conversation)
    const resolved = await this.ctx.sessionController.resolveAgent(sessionId)
    if ('error' in resolved) throw new HubError('failed', `the session could not be opened: ${String(resolved.error)}`)
    const agent = resolved.agent
    if (agent.status !== 'idle' || this.bySession.has(sessionId)) throw new HubError('busy', 'this conversation is already busy')

    let resolve!: (text: string) => void
    let reject!: (error: HubError) => void
    const settled = new Promise<string>((res, rej) => {
      resolve = res
      reject = rej
    })
    const run: Run = { callId: call.id, call, sessionId, agent, tools: new Map(), approvals: new Set(), final: '', resolve, reject }
    this.bySession.set(sessionId, run)
    this.byCall.set(call.id, run)
    const timer = setTimeout(() => {
      agent.cancel({ kind: 'hook', reason: 'nanomuse: the task took longer than the hub allows' })
      run.reject(new HubError('timeout', 'the task took longer than the hub allows'))
    }, TASK_TIMEOUT_MS)
    this.options.log('info', `nanomuse task: ${from.name || from.id} asked: ${text.replace(/\s+/g, ' ').slice(0, 80)}`)
    this.options.notice?.(from.name || from.id, text.replace(/\s+/g, ' ').slice(0, 120))
    try {
      agent.followup(createUserMessage({ content: [{ type: 'text', text: frame(from.name || from.id, this.options.deviceName(), text) }], source: { kind: 'user' } }))
      const final = await settled
      return { text: final, conversation, thread: sessionId, device: this.options.deviceName() }
    } finally {
      clearTimeout(timer)
      this.bySession.delete(sessionId)
      this.byCall.delete(call.id)
      for (const id of run.approvals) this.pendingApprovals.get(id)?.('cancelled')
    }
  }

  /** `stop {call}` → `{stopped}`: the run opened for that call ends; `conversation` picks by place instead. */
  async stop(args: Record<string, unknown>, call: IncomingCall): Promise<Record<string, unknown>> {
    const byCall = this.byCall.get(String(args.call ?? ''))
    const sessionId = this.conversations.get(key(call.from.id, String(args.conversation ?? '').trim() || call.from.id))
    const run = byCall ?? (sessionId ? this.bySession.get(sessionId) : undefined)
    if (!run) return { stopped: false }
    run.agent.cancel({ kind: 'user' })
    return { stopped: true }
  }

  /** `approve {approval_id, allow}` → `{ok}`: the asker's answer to a card relayed from a run here. */
  async approve(args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const decide = this.pendingApprovals.get(String(args.approval_id ?? ''))
    if (!decide) return { ok: false }
    decide(args.allow === true || args.allow === 'true' ? 'approved' : 'denied')
    return { ok: true }
  }

  private async sessionFor(from: Caller, conversation: string): Promise<SessionId> {
    const k = key(from.id, conversation)
    const known = this.conversations.get(k) ?? (this.options.recall?.(k) as SessionId | undefined)
    if (known) {
      // Still there (live, or on disk and resumable)? Else a new one.
      const resolved = await this.ctx.sessionController.resolveAgent(known).catch(() => undefined)
      if (resolved && 'agent' in resolved) {
        this.conversations.set(k, known)
        return known
      }
    }
    const created = await this.ctx.sessionController.create({ agentPreset: 'nanomuse' })
    const title = `From ${from.name || from.id}`
    await this.ctx.sessionController.rename({ sessionId: created.sessionId, title }).catch(() => undefined)
    this.conversations.set(k, created.sessionId)
    this.options.remember?.(k, created.sessionId)
    return created.sessionId
  }

  private onEvent(session: Session, event: SessionEvent): void {
    const run = this.bySession.get(session.id)
    if (!run) return
    switch (event.type) {
      case 'tool/call': {
        const { callId, name, arguments: args } = event.data
        run.tools.set(callId, { name, args })
        run.call.event({ stage: 'tool', id: callId, name, summary: preview(name, args).slice(name.length + 2) })
        return
      }
      case 'tool/result': {
        const message = event.data.message
        const id = String(message.toolCallId)
        run.call.event({
          stage: 'tool_result',
          id,
          name: run.tools.get(id)?.name ?? '',
          ok: message.isError !== true,
          summary: (event.data.error?.reason ?? textOf(message.content)).slice(0, 200),
        })
        return
      }
      case 'assistant/message': {
        const text = textOf(event.data.message.content)
        if (!text) return
        run.final = text
        if (event.data.interrupted !== true) run.call.event({ stage: 'text', text, interim: true })
        return
      }
      case 'turn/end': {
        const reason = event.data.reason
        if (reason.kind === 'error') {
          run.call.event({ stage: 'error', message: String(reason.error.message ?? 'the model failed') })
          run.reject(new HubError('failed', String(reason.error.message ?? 'the model failed')))
        } else if (reason.kind === 'aborted') {
          run.resolve(run.final || '(stopped)')
        } else {
          run.resolve(run.final)
        }
        this.options.notice?.(run.call.from.name || run.call.from.id, run.final ? run.final.replace(/\s+/g, ' ').slice(0, 120) : '')
        return
      }
      default:
        return
    }
  }

  private async onApproval(req: ApprovalRequestEvent, next: () => Promise<ApprovalOutcome>): Promise<ApprovalOutcome> {
    const run = req.agent ? this.bySession.get(req.agent.id) : undefined
    if (!run) return next()
    this.approvalSeq += 1
    const id = `ap_${Date.now().toString(36)}${this.approvalSeq.toString(36)}`
    const tool = req.callId ? run.tools.get(String(req.callId)) : undefined
    run.call.event({
      stage: 'approval',
      approval_id: id,
      preview: tool ? preview(tool.name, tool.args) : req.toolName,
      risk: 'moderate',
      reason: req.displayReason?.en ?? req.reason ?? '',
      device: this.options.deviceName(),
      timeout: APPROVAL_TIMEOUT_S,
    })
    run.approvals.add(id)
    const status = await new Promise<ApprovalStatus>((resolve) => {
      const done = (s: ApprovalStatus) => {
        clearTimeout(timer)
        req.signal?.removeEventListener('abort', onAbort)
        this.pendingApprovals.delete(id)
        run.approvals.delete(id)
        resolve(s)
      }
      const onAbort = () => done('cancelled')
      const timer = setTimeout(() => done('expired'), APPROVAL_TIMEOUT_S * 1000)
      req.signal?.addEventListener('abort', onAbort, { once: true })
      this.pendingApprovals.set(id, done)
    })
    if (status !== 'cancelled') run.call.event({ stage: 'approval_result', approval_id: id, status })
    this.options.log('info', `nanomuse task: ${run.call.from.name || run.call.from.id} ${status} ${req.toolName}`)
    return status === 'approved' ? 'allowed-once' : status === 'cancelled' ? 'cancelled' : 'rejected'
  }
}

function key(deviceId: string, conversation: string): string {
  return `${deviceId}\n${conversation}`
}
