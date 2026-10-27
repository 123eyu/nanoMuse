/**
 * A scheduled task's approval card, pushed to the account's phones (`docs/hub.md`, "A computer's
 * own cards on the phone"). A scheduled run asks while nobody may be at this computer; the card
 * goes out as `event {to:"*", body:{stage:"approval", …, any_device:true}}`, which the relay
 * passes to the devices announcing `approve.any`. The first answer decides it: on this screen,
 * or a phone's `approve {approval_id, allow, scope?}`. Once decided, or let go, an
 * `approval_result {approval_id, status, by?}` closes the card everywhere.
 *
 * Plain state over the hub's `push`; the service in `cloud.ts` wires it to the approval desk,
 * and the tests drive it with a fake hub.
 */

/** What a phone announces in its hello to be shown these cards. */
export const APPROVE_ANY = 'approve.any'

/** How long a phone shows the card before it lets it go; the card here keeps waiting. */
export const PHONE_CARD_SECONDS = 600

/** What a phone may answer besides *Deny*: run this step, or run it and stop asking in this task. */
export type PhoneScope = 'once' | 'schedule'

/** How a card ended, as `approval_result` says it. */
export type CardStatus = 'approved' | 'denied' | 'cancelled'

/** Who answered: a device of the account, this computer included. */
export interface Answerer {
  id: string
  name: string
}

/** The card as the phones read it, the body of the pushed `approval` event without its stage. */
export interface PhoneCard {
  approval_id: string
  /** What the step does, in English and in Chinese ('' when the agent gave only one). */
  preview: string
  preview_zh: string
  /** Why the agent wants it. */
  reason: string
  /** The app the hands would act in; '' for any other tool. */
  app: string
  /** The scheduled task whose run asks, and the chat it lives in. */
  task: { id: string; title: string }
  chat: string
  /** The answers the card offers besides *Deny*: `schedule` only for a hands step in one task. */
  scopes: PhoneScope[]
  /** Seconds the phone shows the card. */
  timeout: number
  /** This computer's name, for the card's title. */
  device: string
}

export interface PhonePushHub {
  /** Push an event to the devices that can answer it; false when the relay does not take pushes. */
  push(to: string, body: Record<string, unknown>): boolean
}

/** A phone's `approve` args read as an answer to a card of ours; undefined for anything else. */
export function phoneAnswer(args: Record<string, unknown>, offered: readonly PhoneScope[]): { allow: boolean; scope: PhoneScope } | undefined {
  const allow = args.allow === true || args.allow === 'true'
  if (!allow) return { allow: false, scope: 'once' }
  const scope = args.scope === undefined ? 'once' : args.scope
  return scope === 'once' || scope === 'schedule' ? (offered.includes(scope) ? { allow, scope } : undefined) : undefined
}

export class PhoneApprovals {
  /** The cards out on the phones, with what each offers and who answered it, once someone did. */
  private readonly out = new Map<string, { scopes: PhoneScope[]; by?: Answerer }>()

  /**
   * @param hub - the push.
   * @param self - this computer, named in `approval_result` when the card was answered here.
   * @param log - one line per thing worth knowing later.
   */
  constructor(
    private readonly hub: PhonePushHub,
    private readonly self: () => Answerer,
    private readonly log: (text: string) => void = () => undefined,
  ) {}

  /** Whether a card of this id is out on the phones. */
  has(id: string): boolean {
    return this.out.has(id)
  }

  /** Push the card; false when it is out already or the relay does not take pushes. */
  ask(card: PhoneCard): boolean {
    const id = card.approval_id
    if (this.out.has(id)) return false
    if (!this.hub.push('*', { stage: 'approval', ...card, any_device: true })) return false
    this.out.set(id, { scopes: [...card.scopes] })
    this.log(`approval ${id}: pushed to the phones`)
    return true
  }

  /** A device's `approve` for a card of ours; undefined when the card is not ours or the answer is not one it offered. */
  answer(args: Record<string, unknown>): { allow: boolean; scope: PhoneScope } | undefined {
    const entry = this.out.get(String(args.approval_id ?? ''))
    return entry && !entry.by ? phoneAnswer(args, entry.scopes) : undefined
  }

  /**
   * The desk took a device's answer: that device is named in the `approval_result`. Called
   * right after the desk decides, before it lets the card go (which happens after an await).
   */
  answeredBy(id: string, from: Answerer): void {
    const entry = this.out.get(id)
    if (entry) entry.by = { id: from.id, name: from.name }
  }

  /** The desk let the card go: decided somewhere, or cancelled. The phones still showing it drop it. */
  close(id: string, status: CardStatus): void {
    const entry = this.out.get(id)
    if (!entry) return
    this.out.delete(id)
    const by = status === 'cancelled' ? undefined : (entry.by ?? this.self())
    this.hub.push('*', { stage: 'approval_result', approval_id: id, status, ...(by ? { by } : {}) })
    this.log(`approval ${id}: ${status}${by ? ` on ${by.name || by.id}` : ''}`)
  }
}
