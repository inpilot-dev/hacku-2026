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
  const [uncertain, setUncertain] = useState<string[]>([]);
  const uncertainRef = useRef(uncertain);
  uncertainRef.current = uncertain;
  const actionRef = useRef('');
  const actionVersion = useRef(0);
  const [acting, setActing] = useState('');  // purchase id with an approve/cancel in flight
  const [error, setError] = useState('');
  const [needsProfile, setNeedsProfile] = useState(false);
  const [refreshError, setRefreshError] = useState('');
  const [retryTick, setRetryTick] = useState(0);
  const [restoring, setRestoring] = useState(true);
  const latest = useRef(purchases);
  latest.current = purchases;

  const upsert = useCallback((next: Purchase) => {
    setPurchases((current) => current.some((p) => p.id === next.id) ? current.map((p) => (p.id === next.id ? next : p)) : [...current, next]);
  }, []);

  // Merge restored runs: a delayed restore must never overwrite a new request.
  useEffect(() => {
    let cancelled = false;
    setRestoring(true);
    const ids = savedIds();
    if (!ids.length) { setRestoring(false); return; }
    void Promise.all(ids.map(async (id) => {
      try { return { purchase: await api.purchase(TOKEN, id), unavailable: false }; }
      catch (error) { return { purchase: null, unavailable: !(error instanceof ApiError && error.status === 404) }; }
    })).then((found) => {
      if (cancelled) return;
      setPurchases((current) => [...found.flatMap((r) => r.purchase && !current.some((p) => p.id === r.purchase!.id) ? [r.purchase] : []), ...current]);
      setRefreshError(found.some((r) => r.unavailable) ? 'Couldn’t load your saved purchases. Retry to check their status.' : '');
      setRestoring(found.some((r) => r.unavailable));
    });
    return () => { cancelled = true; };
  }, [retryTick]);

  // The next poll is scheduled even when an individual status request fails.
  const workingIds = purchases.filter((p) => isWorking(p) || p.status === 'awaiting_approval' || uncertain.includes(p.id)).map((p) => p.id).join(',');
  useEffect(() => {
    if (!workingIds) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      let failed = false;
      for (const id of workingIds.split(',')) {
        if (cancelled) return;
        if (actionRef.current === id) continue;
        const version = actionVersion.current;
        try {
          const next = await api.purchase(TOKEN, id);
          if (!cancelled && version === actionVersion.current && actionRef.current !== id) {
            upsert(next);
            uncertainRef.current = uncertainRef.current.filter((pending) => pending !== id);
            setUncertain(uncertainRef.current);
          }
        } catch (error) {
          if (version !== actionVersion.current || actionRef.current === id) continue;
          if (error instanceof ApiError && error.status === 404) {
            const previous = latest.current.find((p) => p.id === id);
            if (previous && previous.status !== 'paying' && !uncertainRef.current.includes(id) && !cancelled) upsert({ ...previous, status: 'failed', message: 'This search is no longer available. Please start a new search.' });
            else failed = true;
          } else failed = true;
        }
      }
      if (!cancelled) {
        setRefreshError(failed ? 'Connection interrupted. Retrying the saved purchase status…' : '');
        timer = setTimeout(() => void poll(), 2000);
      }
    };
    timer = setTimeout(() => void poll(), 2000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [workingIds, upsert, retryTick]);

  async function start(text: string): Promise<boolean> {
    const request = text.trim();
    if (!request || sending || restoring || actionRef.current || uncertainRef.current.length || latest.current.some((p) => isWorking(p) || p.status === 'awaiting_approval')) return false;
    setSending(true); setError(''); setNeedsProfile(false);
    try {
      const created = await api.startPurchase(TOKEN, request);
      upsert(created);
      try { sessionStorage.setItem(IDS_KEY, JSON.stringify([...savedIds(), created.id].slice(-20))); } catch { /* The current request still exists on the server. */ }
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.details.reason === 'PROFILE_INCOMPLETE') setNeedsProfile(true);
      else setError(err instanceof Error ? err.message : 'The agent could not start.');
      return false;
    } finally { setSending(false); }
  }

  async function submitAction(p: Purchase, operation: () => Promise<Purchase>) {
    if (actionRef.current || uncertainRef.current.includes(p.id)) return;
    actionVersion.current += 1;
    actionRef.current = p.id;
    setActing(p.id); setError('');
    try { upsert(await operation()); }
    catch (err) {
      // A lost response does not say whether the server accepted the action.
      // Read the authoritative status before offering another approval or cancellation.
      try {
        const current = await api.purchase(TOKEN, p.id);
        upsert(current);
        if (current.status === p.status) setError(err instanceof Error ? err.message : 'The request was not accepted.');
      } catch {
        uncertainRef.current = [...uncertainRef.current, p.id];
        setUncertain(uncertainRef.current);
        setRefreshError('Connection interrupted. Checking the purchase status before you can continue…');
      }
    } finally { actionRef.current = ''; setActing(''); }
  }

  async function approve(p: Purchase) {
    if (!p.order) return;
    await submitAction(p, () => api.approvePurchase(TOKEN, p.id, p.order!.total_minor));
  }

  async function cancel(p: Purchase) {
    await submitAction(p, () => api.cancelPurchase(TOKEN, p.id));
  }

  function clear() {
    if (actionRef.current || uncertainRef.current.length || latest.current.some((p) => isWorking(p) || p.status === 'awaiting_approval')) return;
    sessionStorage.removeItem(IDS_KEY);
    setPurchases([]); setError(''); setNeedsProfile(false); setRefreshError('');
  }

  const busy = restoring || uncertain.length > 0 || purchases.some(isWorking) || purchases.some((p) => p.status === 'awaiting_approval');
  return { purchases, uncertain, refreshError, restoring, retry: () => setRetryTick((value) => value + 1), start, approve, cancel, clear, sending, acting, error, setError, needsProfile, setNeedsProfile, busy };
}
