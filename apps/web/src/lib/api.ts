import type { AgentRun, AgentRunRequest, AuditExport, BudgetResponse, CatalogResponse, Checkpoint, CheckpointRequest, ConfirmRequest, DemoPurchaseRequest, DemoPurchaseResponse, DraftRequest, DraftResponse, EventsResponse, Mandate, Quote, QuoteRequest, Receipt, RevokeResponse, VerificationRequest, VerificationResult, VerifierRequest, VerifierResult } from '../../../../contracts/types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  constructor(status: number, message: string, code = 'REQUEST_FAILED', retryable = false) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

const API_ROOT = '/api/v1';
function sessionKey(operation: string) {
  const storageKey = `mandate-idempotency-${operation}`;
  const saved = sessionStorage.getItem(storageKey);
  if (saved) return saved;
  const generated = crypto.randomUUID();
  sessionStorage.setItem(storageKey, generated);
  return generated;
}

async function semanticSessionKey(operation: string, requestBody: unknown) {
  const serialized = JSON.stringify(requestBody);
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(serialized));
  const fingerprint = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  const storageKey = `mandate-idempotency-${operation}`;
  try {
    const previous = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null') as { fingerprint?: string; key?: string } | null;
    if (previous?.fingerprint === fingerprint && previous.key) return previous.key;
  } catch { /* Replace stale or malformed local idempotency state. */ }
  const key = crypto.randomUUID();
  sessionStorage.setItem(storageKey, JSON.stringify({ fingerprint, key }));
  return key;
}

export async function request<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
  const raw = await response.text();
  let body: unknown;
  try { body = raw ? JSON.parse(raw) : null; } catch { body = raw; }
  if (!response.ok) {
    const error = typeof body === 'object' && body !== null && 'error' in body
      ? (body as { error?: { message?: string; code?: string; retryable?: boolean } }).error : undefined;
    throw new ApiError(response.status, error?.message ?? `Request failed (${response.status})`, error?.code, error?.retryable);
  }
  return body as T;
}

export const api = {
  health: () => fetch(`${API_ROOT}/health`).then(async (response) => {
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return response.json() as Promise<{ status: string; payment_mode: string; server_time: string }>;
  }),
  mandate: (token: string, id: string) => request<Mandate>(`/mandates/${encodeURIComponent(id)}`, token),
  draft: async (token: string, input: DraftRequest) => request<DraftResponse>('/mandates/draft', token, {
    method: 'POST', headers: { 'Idempotency-Key': await semanticSessionKey('mandate-draft', input) }, body: JSON.stringify(input),
  }),
  confirm: async (token: string, input: ConfirmRequest) => request<Mandate>('/mandates/confirm', token, {
    method: 'POST', headers: { 'Idempotency-Key': await semanticSessionKey('confirm-draft-demo', input) }, body: JSON.stringify(input),
  }),
  revoke: (token: string, id: string) => request<RevokeResponse>(`/mandates/${encodeURIComponent(id)}/revoke`, token, {
    method: 'POST', headers: { 'Idempotency-Key': sessionKey(`revoke-${id}`) }, body: JSON.stringify({ reason: 'Revoked from Mandate dashboard' }),
  }),
  budget: (token: string, id: string) => request<BudgetResponse>(`/wallet/${encodeURIComponent(id)}`, token),
  catalog: (token: string, merchantId?: string) => request<CatalogResponse>(`/catalog${merchantId ? `?merchant_id=${encodeURIComponent(merchantId)}` : ''}`, token),
  quote: (token: string, input: QuoteRequest) => request<Quote>('/quotes', token, { method: 'POST', body: JSON.stringify(input) }),
  quoteById: (token: string, id: string) => request<Quote>(`/quotes/${encodeURIComponent(id)}`, token),
  startAgentRun: async (token: string, input: AgentRunRequest) => request<AgentRun>('/agent-runs', token, {
    method: 'POST', headers: { 'Idempotency-Key': await semanticSessionKey('agent-run', input) }, body: JSON.stringify(input),
  }),
  agentRun: (token: string, id: string) => request<AgentRun>(`/agent-runs/${encodeURIComponent(id)}`, token),
  demoPurchase: (token: string, input: DemoPurchaseRequest) => request<DemoPurchaseResponse>('/demo/purchases', token, { method: 'POST', body: JSON.stringify(input) }),
  paymentByTransaction: (token: string, transactionId: string) => request<Receipt>(`/payments/${encodeURIComponent(transactionId)}`, token),
  events: (token: string, after = 0, limit = 50) => request<EventsResponse>(`/events?after=${after}&limit=${limit}`, token),
  auditExport: (token: string) => request<AuditExport>('/audit/export', token),
  createCheckpoint: (token: string, input: CheckpointRequest) => request<Checkpoint>('/audit/checkpoints', token, { method: 'POST', headers: { 'Idempotency-Key': sessionKey(`checkpoint-${input.stream_id}`) }, body: JSON.stringify(input) }),
  verifyAudit: (token: string, input: VerifierRequest) => request<VerifierResult>('/verifier/check', token, { method: 'POST', body: JSON.stringify(input) }),
  verifyModel: (token: string, input: VerificationRequest) => request<VerificationResult>('/verification/runs', token, { method: 'POST', body: JSON.stringify(input) }),
};
