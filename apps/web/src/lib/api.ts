import type { BudgetResponse, CatalogResponse, ConfirmRequest, DemoPurchaseRequest, DemoPurchaseResponse, Mandate, Quote, QuoteRequest, RevokeResponse } from '../../../../contracts/types';

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
  confirm: (token: string, input: ConfirmRequest) => request<Mandate>('/mandates/confirm', token, {
    method: 'POST', headers: { 'Idempotency-Key': sessionKey('confirm-draft-demo') }, body: JSON.stringify(input),
  }),
  revoke: (token: string, id: string) => request<RevokeResponse>(`/mandates/${encodeURIComponent(id)}/revoke`, token, {
    method: 'POST', headers: { 'Idempotency-Key': sessionKey(`revoke-${id}`) }, body: JSON.stringify({ reason: 'Revoked from Mandate dashboard' }),
  }),
  budget: (token: string, id: string) => request<BudgetResponse>(`/wallet/${encodeURIComponent(id)}`, token),
  catalog: (token: string, merchantId?: string) => request<CatalogResponse>(`/catalog${merchantId ? `?merchant_id=${encodeURIComponent(merchantId)}` : ''}`, token),
  quote: (token: string, input: QuoteRequest) => request<Quote>('/quotes', token, { method: 'POST', body: JSON.stringify(input) }),
  demoPurchase: (token: string, input: DemoPurchaseRequest) => request<DemoPurchaseResponse>('/demo/purchases', token, { method: 'POST', body: JSON.stringify(input) }),
};
