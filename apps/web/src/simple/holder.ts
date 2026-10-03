/*
 * Who an allowance is for: a display name only. The wallet has no such field and enforces nothing by it,
 * so it lives on this device, keyed by mandate. An empty name means the user is shopping for themselves.
 */

const KEY = 'mandate-holder-names-v1';

function all(): Record<string, string> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(KEY) ?? '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, string> : {};
  } catch { return {}; }
}

export function holderName(mandateId: string | null | undefined): string {
  const name = mandateId ? all()[mandateId] : '';
  return typeof name === 'string' ? name.trim().slice(0, 40) : '';
}

export function saveHolderName(mandateId: string, name: string) {
  try { localStorage.setItem(KEY, JSON.stringify({ ...all(), [mandateId]: name.trim().slice(0, 40) })); } catch { /* display only */ }
}

/** "Mum’s", "James’", or "Your" when the allowance is the user's own. */
export function possessive(name: string) {
  if (!name) return 'Your';
  return /s$/i.test(name) ? `${name}’` : `${name}’s`;
}
