import type { AttackStep, AuditEvent } from '../../../../contracts/types';
import { money } from '../lib/format';

/*
 * The harness drawn as five gates in front of the payment rail. Which gate stopped a request is read from the
 * wallet's own violation code or HTTP status; nothing here decides an outcome.
 */

export const GATES = [
  { id: 'identity', name: 'Identity', detail: 'role + household scope' },
  { id: 'price', name: 'Trusted price', detail: 'catalog, not the agent' },
  { id: 'rules', name: 'Owner’s rules', detail: 'shops, categories, expiry' },
  { id: 'budget', name: 'Budget lock', detail: 'atomic, every parent' },
  { id: 'capability', name: 'Signed capability', detail: 'single use, 120 s' },
] as const;
export const RAIL = GATES.length; // stop index for "reached the payment rail"

const CODE_GATE: Record<string, number> = {
  QUOTE_EXPIRED: 1,
  MERCHANT_NOT_ALLOWED: 2, CATEGORY_BLOCKED: 2, CATEGORY_REVIEW_REQUIRED: 2, MANDATE_EXPIRED: 2, MANDATE_REVOKED: 2,
  MANDATE_NOT_ACTIVE: 2, APPROVAL_REQUIRED: 2, APPROVAL_DENIED: 2, APPROVAL_EXPIRED: 2, CARD_FROZEN: 2,
  RISK_REVIEW_REQUIRED: 2, VELOCITY_LIMIT_EXCEEDED: 2,
  ORDER_CAP_EXCEEDED: 3, PERIOD_BUDGET_EXCEEDED: 3,
  AUTHORIZATION_INVALID: 4, AUTHORIZATION_EXPIRED: 4, QUOTE_CHANGED: 4, RESERVATION_CANCELLED: 4,
  RESERVATION_EXPIRED: 4, MANDATE_VERSION_CHANGED: 4,
};

/** Short, plain-English reason for each wallet refusal code (the code itself stays in the trace). */
const REASON: Record<string, string> = {
  CATEGORY_BLOCKED: 'Blocked category', CATEGORY_REVIEW_REQUIRED: 'Unknown item, asks owner', MERCHANT_NOT_ALLOWED: 'Shop not allowed',
  ORDER_CAP_EXCEEDED: 'Over the per-order cap', PERIOD_BUDGET_EXCEEDED: 'Weekly budget used up', AUTHORIZATION_INVALID: 'Forged signature',
  AUTHORIZATION_EXPIRED: 'Approval expired', QUOTE_CHANGED: 'Basket was swapped', MANDATE_REVOKED: 'Access revoked',
  MANDATE_EXPIRED: 'Allowance expired', MANDATE_NOT_ACTIVE: 'Allowance not active', APPROVAL_REQUIRED: 'Needs owner approval',
  APPROVAL_DENIED: 'Owner said no', APPROVAL_EXPIRED: 'Owner did not answer', CARD_FROZEN: 'Card frozen',
  VELOCITY_LIMIT_EXCEEDED: 'Too many orders', RISK_REVIEW_REQUIRED: 'Held for review', RESERVATION_CANCELLED: 'Reservation closed',
  RESERVATION_EXPIRED: 'Reservation expired', MANDATE_VERSION_CHANGED: 'Rules changed', QUOTE_EXPIRED: 'Price expired',
};
const HTTP_REASON: Record<number, string> = { 401: 'No valid token', 403: 'Agents can’t do that', 404: 'Not their allowance', 422: 'Agents can’t set prices' };
export const reason = (code: string | undefined) => (code && REASON[code]) || code || 'Refused';

export type PacketKind = 'agent' | 'swarm' | 'owner' | 'stranger';
export type PacketResult = 'pass' | 'block' | 'review' | 'info';
export type Packet = {
  id: string;
  who: string;       // short label drawn on the packet
  kind: PacketKind;
  stopAt: number;    // gate index, or RAIL
  result: PacketResult;
  tag: string;       // what the stopping gate says
  note: string;      // what was attempted, in plain English
  burst?: boolean;   // launch together with the previous packet (a race)
  group?: string;    // the attack run this packet belongs to
};

export function gateForCode(code: string | undefined) {
  return code && code in CODE_GATE ? CODE_GATE[code] : 2;
}

function kindForActor(actor: string): PacketKind {
  if (actor === 'user') return 'owner';
  if (actor.startsWith('stranger')) return 'stranger';
  if (actor.startsWith('swarm')) return 'swarm';
  return 'agent';
}

function whoForActor(actor: string) {
  if (actor === 'user') return 'Owner';
  if (actor === 'agent') return 'Agent';
  if (actor === 'stranger') return 'Outsider';
  if (actor === 'stranger_agent') return 'Rogue agent';
  if (actor.startsWith('swarm_agent_')) return `A${actor.slice('swarm_agent_'.length)}`;
  return actor;
}

/** One recorded lab request → one packet. */
export function packetForStep(step: AttackStep, id: string, burst: boolean): Packet {
  const base = { id, who: whoForActor(step.actor), kind: kindForActor(step.actor), burst, note: step.title };
  const response = step.response as { violations?: { code: string }[]; receipt?: { amount_minor?: number }; total_minor?: number; error?: { code?: string } };
  if (step.http_status === 401 || step.http_status === 403 || step.http_status === 404) {
    return { ...base, stopAt: 0, result: 'block', tag: HTTP_REASON[step.http_status] };
  }
  if (step.http_status === 422) return { ...base, stopAt: 1, result: 'block', tag: HTTP_REASON[422] };
  if (step.http_status >= 400) return { ...base, stopAt: 0, result: 'block', tag: `HTTP ${step.http_status}` };
  if (step.outcome.startsWith('refused') || step.outcome.startsWith('requires_review')) {
    const code = response.violations?.[0]?.code;
    return { ...base, stopAt: gateForCode(code), result: step.outcome.startsWith('refused') ? 'block' : 'review', tag: reason(code) };
  }
  if (step.path === '/payments' && step.outcome.startsWith('completed')) {
    const amount = response.receipt?.amount_minor;
    const replay = step.outcome.includes('replayed');
    return { ...base, stopAt: RAIL, result: replay ? 'info' : 'pass', tag: replay ? 'Replay: same receipt, no charge' : amount !== undefined ? `Paid ${money(amount)}` : 'Paid' };
  }
  if (step.path === '/authorizations') return { ...base, stopAt: 4, result: 'pass', tag: 'Approved + reserved' };
  if (step.path === '/quotes') {
    return { ...base, stopAt: 1, result: 'pass', tag: response.total_minor !== undefined ? `Priced ${money(response.total_minor)}` : 'Priced' };
  }
  if (step.path.endsWith('/revoke')) return { ...base, stopAt: 2, result: 'info', tag: 'Owner revoked access' };
  return { ...base, stopAt: 0, result: 'info', tag: step.outcome };
}

/** One live wallet audit event → a packet, or null for events that are only shown in the feed. */
export function packetForEvent(event: AuditEvent): Packet | null {
  const p = event.payload as { amount_minor?: number; total_minor?: number; violations?: { code: string }[]; status?: string; receipt?: { amount_minor?: number } };
  const base = { id: `ev-${event.stream_id}-${event.sequence}`, who: event.actor_id.startsWith('agent') ? 'Agent' : 'Owner', kind: (event.actor_id.startsWith('agent') ? 'agent' : 'owner') as PacketKind, note: describeEvent(event) };
  switch (event.type) {
    case 'quote_created': return { ...base, stopAt: 1, result: 'pass', tag: p.total_minor !== undefined ? `Priced ${money(p.total_minor)}` : 'Priced' };
    case 'authorization_approved': return { ...base, stopAt: 4, result: 'pass', tag: p.amount_minor !== undefined ? `Reserved ${money(p.amount_minor)}` : 'Reserved' };
    case 'authorization_refused': {
      const code = p.violations?.[0]?.code;
      return { ...base, stopAt: gateForCode(code), result: p.status === 'requires_review' ? 'review' : 'block', tag: reason(code) };
    }
    case 'payment_completed': return { ...base, stopAt: RAIL, result: 'pass', tag: p.receipt?.amount_minor !== undefined ? `Paid ${money(p.receipt.amount_minor)}` : 'Paid' };
    case 'payment_refused': {
      const code = p.violations?.[0]?.code;
      return { ...base, stopAt: gateForCode(code), result: 'block', tag: reason(code) };
    }
    case 'mandate_confirmed': return { ...base, stopAt: 2, result: 'info', tag: 'Rules set' };
    case 'mandate_revoked': return { ...base, stopAt: 2, result: 'block', tag: 'Access revoked' };
    case 'card_frozen': return { ...base, stopAt: 2, result: 'block', tag: 'Card frozen' };
    case 'card_unfrozen': return { ...base, stopAt: 2, result: 'info', tag: 'Card resumed' };
    default: return null;
  }
}

export function describeEvent(event: AuditEvent) {
  const p = event.payload as { amount_minor?: number; total_minor?: number; merchant_id?: string; violations?: { code: string; message?: string }[]; status?: string; receipt?: { amount_minor?: number; merchant_id?: string } };
  switch (event.type) {
    case 'quote_created': return `Wallet priced a basket at ${p.merchant_id ?? 'a shop'}${p.total_minor !== undefined ? `: ${money(p.total_minor)}` : ''}`;
    case 'authorization_approved': return `Approved and reserved${p.amount_minor !== undefined ? ` ${money(p.amount_minor)}` : ''}; single-use capability signed`;
    case 'authorization_refused': return p.status === 'requires_review' ? `Held for the owner: ${p.violations?.[0]?.code ?? 'review'}` : `Refused: ${p.violations?.[0]?.message ?? p.violations?.[0]?.code ?? 'policy'}`;
    case 'payment_completed': return `Paid${p.receipt?.amount_minor !== undefined ? ` ${money(p.receipt.amount_minor)}` : ''} on the sandbox rail`;
    case 'payment_refused': return `Payment refused: ${p.violations?.[0]?.message ?? p.violations?.[0]?.code ?? ''}`;
    case 'mandate_confirmed': return 'Owner confirmed spending rules';
    case 'mandate_revoked': return 'Owner revoked the allowance';
    case 'reservation_cancelled': return 'Reserved money released';
    case 'reservation_expired': return 'Unused reservation expired and was released';
    case 'card_frozen': return 'Owner froze the card';
    case 'card_unfrozen': return 'Owner resumed the card';
    case 'agent_run_updated': return 'Shopping agent progress';
    default: return (event.type as string).replace(/_/g, ' ');
  }
}
