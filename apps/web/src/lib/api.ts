import type { AttackList, AttackResult, AgentRun, AgentRunRequest, ApprovalDecisionResponse, ApprovalList, ApprovalRequest, AuditExport, BudgetResponse, CatalogResponse, Checkpoint, CheckpointRequest, ConfirmRequest, DemoPurchaseRequest, DemoPurchaseResponse, DraftRequest, DraftResponse, EventsResponse, Mandate, PaymentOptionsResponse, Quote, QuoteRequest, Receipt, RevokeResponse, VerificationRequest, VerificationResult, VerifierRequest, VerifierResult, ShoppingListParseRequest, ShoppingListParseResponse, TranscriptionRequest, TranscriptionResponse, StoreList, StoreConnection, StoreLoginTicket, CartSyncRequest, CartSyncResult, VirtualCard, CardStatusResponse, CardAuthorizationList, Profile, ProfileInput, Purchase } from '../../../../contracts/types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  readonly details: Record<string, unknown>;
  constructor(status: number, message: string, code = 'REQUEST_FAILED', retryable = false, details: Record<string, unknown> = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.details = details;
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
      ? (body as { error?: { message?: string; code?: string; retryable?: boolean; details?: Record<string, unknown> } }).error : undefined;
    throw new ApiError(response.status, error?.message ?? `Request failed (${response.status})`, error?.code, error?.retryable, error?.details ?? {});
  }
  return body as T;
}

export const api = {
  health: () => fetch(`${API_ROOT}/health`).then(async (response) => {
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return response.json() as Promise<{ status: string; payment_mode: string; server_time: string }>;
  }),
  mandate: (token: string, id: string) => request<Mandate>(`/mandates/${encodeURIComponent(id)}`, token),
  card: (token: string, id: string) => request<VirtualCard>(`/mandates/${encodeURIComponent(id)}/card`, token),
  freezeCard: (token: string, id: string) => request<CardStatusResponse>(`/mandates/${encodeURIComponent(id)}/card/freeze`, token, {
    method: 'POST', headers: { 'Idempotency-Key': sessionKey(`freeze-${id}`) }, body: JSON.stringify({ reason: 'Paused from family dashboard' }),
  }),
  unfreezeCard: (token: string, id: string) => request<CardStatusResponse>(`/mandates/${encodeURIComponent(id)}/card/unfreeze`, token, {
    method: 'POST', headers: { 'Idempotency-Key': sessionKey(`unfreeze-${id}`) }, body: JSON.stringify({ reason: 'Resumed from family dashboard' }),
  }),
  cardAuthorizations: (token: string, id: string) => request<CardAuthorizationList>(`/mandates/${encodeURIComponent(id)}/card/authorizations`, token),
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
  paymentOptions: (token: string, quoteId: string) => request<PaymentOptionsResponse>(`/quotes/${encodeURIComponent(quoteId)}/payment-options`, token),
  startAgentRun: async (token: string, input: AgentRunRequest) => request<AgentRun>('/agent-runs', token, {
    method: 'POST', headers: { 'Idempotency-Key': await semanticSessionKey('agent-run', input) }, body: JSON.stringify(input),
  }),
  agentRun: (token: string, id: string) => request<AgentRun>(`/agent-runs/${encodeURIComponent(id)}`, token),
  parseShoppingList: (token: string, input: ShoppingListParseRequest) => request<ShoppingListParseResponse>('/shopping-list/parse', token, { method: 'POST', body: JSON.stringify(input) }),
  transcribeShoppingList: (token: string, input: TranscriptionRequest) => request<TranscriptionResponse>('/shopping-list/transcribe', token, { method: 'POST', body: JSON.stringify(input) }),
  demoPurchase: (token: string, input: DemoPurchaseRequest) => request<DemoPurchaseResponse>('/demo/purchases', token, { method: 'POST', body: JSON.stringify(input) }),
  paymentByTransaction: (token: string, transactionId: string) => request<Receipt>(`/payments/${encodeURIComponent(transactionId)}`, token),
  approval: (token: string, id: string) => request<ApprovalRequest>(`/approvals/${encodeURIComponent(id)}`, token),
  approvals: (token: string, status?: ApprovalRequest['status']) => request<ApprovalList>(`/approvals${status ? `?status=${encodeURIComponent(status)}` : ''}`, token),
  approve: (token: string, id: string) => request<ApprovalDecisionResponse>(`/approvals/${encodeURIComponent(id)}/approve`, token, {
    method: 'POST', headers: { 'Idempotency-Key': sessionKey(`approval-${id}-approve`) }, body: JSON.stringify({ note: 'Approved in the Mandate family dashboard' }),
  }),
  deny: (token: string, id: string) => request<ApprovalDecisionResponse>(`/approvals/${encodeURIComponent(id)}/deny`, token, {
    method: 'POST', headers: { 'Idempotency-Key': sessionKey(`approval-${id}-deny`) }, body: JSON.stringify({ note: 'Declined in the Mandate family dashboard' }),
  }),
  events: (token: string, after = 0, limit = 50) => request<EventsResponse>(`/events?after=${after}&limit=${limit}`, token),
  auditExport: (token: string) => request<AuditExport>('/audit/export', token),
  createCheckpoint: (token: string, input: CheckpointRequest) => request<Checkpoint>('/audit/checkpoints', token, { method: 'POST', headers: { 'Idempotency-Key': sessionKey(`checkpoint-${input.stream_id}`) }, body: JSON.stringify(input) }),
  verifyAudit: (token: string, input: VerifierRequest) => request<VerifierResult>('/verifier/check', token, { method: 'POST', body: JSON.stringify(input) }),
  verifyModel: (token: string, input: VerificationRequest) => request<VerificationResult>('/verification/runs', token, { method: 'POST', body: JSON.stringify(input) }),
  stores: (token: string) => request<StoreList>('/stores', token),
  store: (token: string, storeId: string) => request<StoreConnection>(`/stores/${encodeURIComponent(storeId)}`, token),
  connectStore: (token: string, storeId: string) => request<StoreConnection>(`/stores/${encodeURIComponent(storeId)}/connect`, token, { method: 'POST' }),
  storeLoginTicket: (token: string, storeId: string) => request<StoreLoginTicket>(`/stores/${encodeURIComponent(storeId)}/login/ticket`, token, { method: 'POST' }),
  disconnectStore: (token: string, storeId: string) => request<StoreConnection>(`/stores/${encodeURIComponent(storeId)}/connection`, token, { method: 'DELETE' }),
  syncCart: (token: string, input: CartSyncRequest) => request<CartSyncResult>('/carts/sync', token, { method: 'POST', body: JSON.stringify(input) }),
  profile: (token: string) => request<Profile>('/profile', token),
  saveProfile: (token: string, input: ProfileInput) => request<Profile>('/profile', token, { method: 'PUT', body: JSON.stringify(input) }),
  startPurchase: (token: string, text: string) => request<Purchase>('/purchases', token, { method: 'POST', body: JSON.stringify({ text }) }),
  purchase: (token: string, id: string) => request<Purchase>(`/purchases/${encodeURIComponent(id)}`, token),
  approvePurchase: (token: string, id: string, totalMinor: number) => request<Purchase>(`/purchases/${encodeURIComponent(id)}/approve`, token, {
    method: 'POST', body: JSON.stringify({ total_minor: totalMinor }),
  }),
  cancelPurchase: (token: string, id: string) => request<Purchase>(`/purchases/${encodeURIComponent(id)}/cancel`, token, { method: 'POST' }),
  attacks: (token: string) => request<AttackList>('/demo/attacks', token),
  runAttack: (token: string, attackId: string) => request<AttackResult>(`/demo/attacks/${encodeURIComponent(attackId)}/runs`, token, { method: 'POST' }),
};

/** WebSocket URL for a store's sign-in stream on this origin (the dev server proxies it). */
export function storeLoginStreamUrl(storeId: string, ticket: string) {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${window.location.host}${API_ROOT}/stores/${encodeURIComponent(storeId)}/login/stream?ticket=${encodeURIComponent(ticket)}`;
}
