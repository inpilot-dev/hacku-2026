import { request } from './api';
import type { Policy, Quote, RuleViolation, PreviewInput, PlanInput, StudyStart, StudyFinish, SandboxStart, GroupStart } from '../../../../contracts/types';
export type Preview = { illustrative: boolean; examples: Array<{ id: string; total_minor: number; category: string; status: string; violations: RuleViolation[]; rule_ids: string[] }> };
export type Plans = { plans: Array<{ total_minor: number; store_count: number; orders: Array<Pick<Quote, 'merchant_id' | 'total_minor' | 'delivery_context_id' | 'items' | 'charges'>> }>; evaluated: number; truncated: boolean; planning_only: boolean; reason: string };
export type Study = StudyStart & Partial<StudyFinish> & { id: string; started_at: string; finished_at: string | null; elapsed_seconds: number | null; quote: Quote };
export type Operation = { id: string; status: string; payment_state: string; order_state: string; recovery_state: string; budget_state: string; amount_minor: number; checkout_url: string | null; quote: Quote; events: Array<{ at: string; message: string }>; boundary: string };
export type BasketGroup = { id: string; status: string; total_minor: number; rollback_requested: boolean; operations: Operation[]; boundary: string };
export const commerce = {
  groups: (token: string) => request<{ groups: BasketGroup[] }>('/commerce/groups', token),
  startGroup: (token: string, input: GroupStart, key: string) => request<BasketGroup>('/commerce/groups', token, { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(input) }),
  advanceGroup: (token: string, id: string) => request<BasketGroup>(`/commerce/groups/${id}/advance`, token, { method: 'POST' }),
  cancelGroup: (token: string, id: string) => request<BasketGroup>(`/commerce/groups/${id}/cancel`, token, { method: 'POST' }),
  preview: (token: string, policy: Policy) => request<Preview>('/commerce/preview', token, { method: 'POST', body: JSON.stringify({ policy } satisfies PreviewInput) }),
  plans: (token: string, input: PlanInput) => request<Plans>('/commerce/plans', token, { method: 'POST', body: JSON.stringify(input) }),
  studies: (token: string) => request<{ runs: Study[] }>('/commerce/studies', token),
  startStudy: (token: string, input: StudyStart) => request<Study>('/commerce/studies', token, { method: 'POST', body: JSON.stringify(input) }),
  finishStudy: (token: string, id: string, input: StudyFinish) => request<Study>(`/commerce/studies/${id}/finish`, token, { method: 'POST', body: JSON.stringify(input) }),
  provider: (token: string) => request<{ configured: boolean; simulated: boolean }>('/commerce/provider', token),
  approval: (token: string, quoteId: string) => request<{ quote: Quote; quote_hash: string }>(`/commerce/quotes/${quoteId}/approval`, token),
  operations: (token: string) => request<{ operations: Operation[] }>('/commerce/operations', token),
  startOperation: (token: string, input: SandboxStart, key: string) => request<Operation>('/commerce/operations', token, { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(input) }),
  advance: (token: string, id: string) => request<Operation>(`/commerce/operations/${id}/advance`, token, { method: 'POST' }),
  cancel: (token: string, id: string) => request<Operation>(`/commerce/operations/${id}/cancel`, token, { method: 'POST' }),
};
