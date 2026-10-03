import { useCallback, useEffect, useRef, useState } from 'react';
import type { Purchase } from '../../../../../contracts/types';
import { api, ApiError } from '@/lib/api';
import { TOKEN } from './account';

/*
 * One-time purchases (POST/GET /purchases): the agent searches the web, checks out as a guest and waits for the
 * user to approve the exact total. This keeps the conversation (this session's purchases) and polls the ones that
 * are still working. The server decides everything; approve sends back exactly the total it showed.
 */

const IDS_KEY = 'mandate-purchase-ids-v1';
const WORKING = new Set<Purchase['status']>(['queued', 'searching', 'checking_out', 'paying']);
export const isWorking = (p: Purchase) => WORKING.has(p.status);

function savedIds(): string[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(IDS_KEY) ?? '[]');
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string').slice(-20) : [];
  } catch { return []; }
}

export function usePurchases() {
  const [purchases, setPurchases] = useState<Purchase[]>([]);
  const [sending, setSending] = useState(false);
  const [acting, setActing] = useState('');  // purchase id with an approve/cancel in flight
  const [error, setError] = useState('');
  const [needsProfile, setNeedsProfile] = useState(false);
  const timer = useRef<number | null>(null);

  const upsert = useCallback((next: Purchase) => {
    setPurchases((current) => current.some((p) => p.id === next.id) ? current.map((p) => (p.id === next.id ? next : p)) : [...current, next]);
  }, []);

  // Restore this session's conversation.
  useEffect(() => {
    const ids = savedIds();
    if (!ids.length) return;
    void Promise.all(ids.map((id) => api.purchase(TOKEN, id).catch(() => null))).then((found) => {
      setPurchases(found.filter((p): p is Purchase => p !== null));
    });
  }, []);

  // Poll every purchase that is still working.
  useEffect(() => {
    const working = purchases.filter(isWorking);
    if (!working.length) return;
    timer.current = window.setTimeout(async () => {
      for (const p of working) {
        try { upsert(await api.purchase(TOKEN, p.id)); } catch { /* keep the last state; try again next tick */ }
      }
    }, 2000);
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [purchases, upsert]);

  async function start(text: string): Promise<boolean> {
    const request = text.trim();
    if (!request || sending) return false;
    setSending(true); setError(''); setNeedsProfile(false);
    try {
      const created = await api.startPurchase(TOKEN, request);
      upsert(created);
      sessionStorage.setItem(IDS_KEY, JSON.stringify([...savedIds(), created.id].slice(-20)));
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.details.reason === 'PROFILE_INCOMPLETE') setNeedsProfile(true);
      else setError(err instanceof Error ? err.message : 'The agent could not start.');
      return false;
    } finally { setSending(false); }
  }

  async function approve(p: Purchase) {
    if (!p.order || acting) return;
    setActing(p.id); setError('');
    try { upsert(await api.approvePurchase(TOKEN, p.id, p.order.total_minor)); } catch (err) {
      setError(err instanceof Error ? err.message : 'The approval was not accepted.');
      try { upsert(await api.purchase(TOKEN, p.id)); } catch { /* shown as is */ }
    } finally { setActing(''); }
  }

  async function cancel(p: Purchase) {
    if (acting) return;
    setActing(p.id); setError('');
    try { upsert(await api.cancelPurchase(TOKEN, p.id)); } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not cancel.');
    } finally { setActing(''); }
  }

  function clear() {
    sessionStorage.removeItem(IDS_KEY);
    setPurchases((current) => current.filter(isWorking));
  }

  const busy = purchases.some(isWorking) || purchases.some((p) => p.status === 'awaiting_approval');
  return { purchases, start, approve, cancel, clear, sending, acting, error, setError, needsProfile, setNeedsProfile, busy };
}
