import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { AuditEvent, BudgetResponse, Mandate, Quote, Receipt, VirtualCard } from '../../../../../contracts/types';
import { api, ApiError } from '@/lib/api';
import { money } from '@/lib/format';
import { newId } from '@/lib/utils';
import { holderName, saveHolderName } from '@/simple/holder';

/*
 * Account state shared by every tab: the allowance (mandate), its budget and virtual card, the activity log and
 * the receipts. Moved from the single-page flow (simple/SimpleApp.tsx) without changing what it does: the wallet
 * stays the only authority, and this only reads it and submits the user's own actions.
 */

export const TOKEN = 'dev-user-token';
const MANDATE_KEY = 'mandate-id';

export type Who = 'kumi' | 'kip' | 'you' | 'bean' | 'stella';
export type LogEntry = { id: string; at: string; who: Who; text: string; tone: 'good' | 'bad' | 'info' };
export type ReceiptRecord = { receipt: Receipt; quote: Quote | null; occurredAt: string };

function eventText(event: AuditEvent): Omit<LogEntry, 'id' | 'at'> | null {
  const amount = typeof event.payload.amount_minor === 'number' ? ` ${money(event.payload.amount_minor)}` : '';
  switch (event.type) {
    case 'mandate_confirmed': return { who: 'bean', text: 'Allowance switched on', tone: 'good' };
    case 'mandate_revoked': return { who: 'kip', text: 'Allowance permanently revoked', tone: 'bad' };
    case 'card_frozen': return { who: 'kip', text: 'Virtual card paused', tone: 'bad' };
    case 'card_unfrozen': return { who: 'kip', text: 'Virtual card resumed', tone: 'good' };
    case 'quote_created': return { who: 'kumi', text: 'Basket priced', tone: 'info' };
    case 'authorization_refused': return event.payload.status === 'requires_review'
      ? { who: 'kip', text: 'Approval requested', tone: 'info' }
      : { who: 'kip', text: 'Purchase refused', tone: 'bad' };
    case 'payment_completed': return { who: 'kip', text: `Paid${amount}`, tone: 'good' };
    case 'payment_refused': return { who: 'kip', text: 'Payment refused', tone: 'bad' };
    default: return null;
  }
}

function useAccountState() {
  const [online, setOnline] = useState<boolean | null>(null);
  const refreshVersion = useRef(0);
  const [mandateId, setMandateId] = useState(() => localStorage.getItem(MANDATE_KEY) ?? '');
  const [mandate, setMandate] = useState<Mandate | null>(null);
  const [budget, setBudget] = useState<BudgetResponse | null>(null);
  const [card, setCard] = useState<VirtualCard | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [refreshError, setRefreshError] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [holder, setHolder] = useState(() => holderName(localStorage.getItem(MANDATE_KEY)));
  const [log, setLog] = useState<LogEntry[]>([]);
  const [serverLog, setServerLog] = useState<LogEntry[] | null>(null);
  // Set while a grocery checkout is unresolved: the card and allowance controls wait for it.
  const [checkoutLocked, setCheckoutLocked] = useState(false);
  // The allowance setup dialog: a new allowance, or changing the current one.
  const [setup, setSetup] = useState<null | 'new' | 'change'>(null);

  const note = useCallback((who: Who, text: string, tone: LogEntry['tone']) => {
    setLog((current) => [{ id: newId(), at: new Date().toISOString(), who, text, tone }, ...current].slice(0, 20));
  }, []);

  const refresh = useCallback(async (id = mandateId) => {
    const version = ++refreshVersion.current;
    try {
      await api.health();
      if (version !== refreshVersion.current) return;
      setOnline(true);
    } catch {
      if (version === refreshVersion.current) setOnline(false);
      return;
    }
    if (!id) { setMandate(null); setBudget(null); setCard(null); setRefreshError(''); setLoaded(true); return; }
    try {
      const [m, b] = await Promise.allSettled([api.mandate(TOKEN, id), api.budget(TOKEN, id)]);
      if (version !== refreshVersion.current) return;
      if (m.status === 'rejected') throw m.reason;
      // A missing budget is not evidence that the allowance itself was deleted.
      if (b.status === 'rejected') throw new Error('Could not refresh the budget.');
      setMandate(m.value); setBudget(b.value); setRefreshError('');
      try {
        const currentCard = await api.card(TOKEN, id);
        if (version === refreshVersion.current) setCard(currentCard);
      } catch (err) {
        if (version === refreshVersion.current && err instanceof ApiError && err.status === 404) setCard(null);
      }
    } catch (err) {
      if (version !== refreshVersion.current) return;
      if (err instanceof ApiError && err.status === 404) { localStorage.removeItem(MANDATE_KEY); setMandateId(''); setMandate(null); setBudget(null); setCard(null); setRefreshError(''); }
      else setRefreshError('Could not refresh your wallet.');
    } finally { if (version === refreshVersion.current) setLoaded(true); }
    try {
      const result = await api.events(TOKEN, 0, 100);
      if (version !== refreshVersion.current) return;
      setServerLog(result.events.filter((event) => !event.mandate_id || event.mandate_id === id).reverse().slice(0, 12).flatMap((event) => {
        const text = eventText(event);
        return text ? [{ ...text, id: event.event_id, at: event.occurred_at }] : [];
      }));
    } catch { /* Keep the last known audit trail during a temporary outage. */ }
  }, [mandateId]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    const check = () => { if (document.visibilityState === 'visible') void refresh(); };
    window.addEventListener('online', check);
    window.addEventListener('focus', check);
    const timer = setInterval(check, online === false ? 5000 : 30000);
    return () => {
      clearInterval(timer);
      window.removeEventListener('online', check);
      window.removeEventListener('focus', check);
    };
  }, [online, refresh]);

  /** A new allowance was confirmed with the wallet; show it, and retire the one it replaces. */
  async function activated(result: Mandate, summary: string, name: string) {
    const replaced = mandate && mandate.status === 'active' && mandate.id !== result.id ? mandate : null;
    localStorage.setItem(MANDATE_KEY, result.id);
    saveHolderName(result.id, name); setHolder(name);
    setMandateId(result.id); setMandate(result);
    note('bean', summary, 'good');
    if (replaced) {
      try {
        await api.revoke(TOKEN, replaced.id);
        sessionStorage.removeItem(`mandate-idempotency-revoke-${replaced.id}`);
        note('kip', 'Previous allowance revoked', 'info');
      } catch (err) {
        setError(`The new allowance is on, but the old one could not be revoked: ${err instanceof Error ? err.message : 'unknown error'}.`);
      }
    }
    await refresh(result.id);
  }

  async function freeze() {
    if (!mandate) return;
    setBusy('freeze'); setError('');
    try {
      const result = await api.freezeCard(TOKEN, mandate.id);
      sessionStorage.removeItem(`mandate-idempotency-freeze-${mandate.id}`);
      setCard(result.card);
      note('kip', 'Card paused. No new purchases can be made.', 'bad');
      await refresh(mandate.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not freeze the card.');
    } finally { setBusy(''); }
  }

  async function unfreeze() {
    if (!mandate) return;
    setBusy('unfreeze'); setError('');
    try {
      const result = await api.unfreezeCard(TOKEN, mandate.id);
      sessionStorage.removeItem(`mandate-idempotency-unfreeze-${mandate.id}`);
      setCard(result.card);
      note('kip', 'Card resumed. The existing allowance still applies.', 'good');
      await refresh(mandate.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not unfreeze the card.');
    } finally { setBusy(''); }
  }

  async function revoke() {
    if (!mandate) return false;
    setBusy('revoke'); setError('');
    try {
      const result = await api.revoke(TOKEN, mandate.id);
      sessionStorage.removeItem(`mandate-idempotency-revoke-${mandate.id}`);
      setMandate(result.mandate);
      note('kip', `Allowance revoked${result.cancelled_reservation_ids.length ? `, ${result.cancelled_reservation_ids.length} hold(s) released` : ''}`, 'bad');
      await refresh(mandate.id);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not revoke the allowance.');
      return false;
    } finally { setBusy(''); }
  }

  const receipts = useReceiptsState(loaded, mandateId);

  const active = mandate?.status === 'active';
  const period = budget?.applicable_budgets[0];
  const limit = period?.limit_minor ?? mandate?.policy.period_limits[0]?.limit_minor ?? 0;
  const spent = (period?.paid_minor ?? 0) + (period?.reserved_minor ?? 0);
  const available = period?.available_minor ?? limit;

  return {
    online, loaded, refreshError, mandateId, mandate, budget, card, holder, busy, error, setError,
    active, limit, spent, available,
    log: serverLog && serverLog.length ? serverLog : log, fromServer: Boolean(serverLog && serverLog.length),
    note, refresh, activated, freeze, unfreeze, revoke,
    checkoutLocked, setCheckoutLocked,
    setup, openSetup: (mode: 'new' | 'change') => setSetup(mode), closeSetup: () => setSetup(null),
    receipts,
  };
}

function useReceiptsState(loaded: boolean, mandateId: string) {
  const [records, setRecords] = useState<ReceiptRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const detailRequestId = useRef(0);
  const loadedOnce = useRef(false);
  const inFlight = useRef(false);

  const load = useCallback(async (force = false) => {
    if (inFlight.current || (loadedOnce.current && !force)) return;
    inFlight.current = true;
    setLoading(true); setError('');
    try {
      const audit = await api.auditExport(TOKEN);
      const paymentEvents = audit.events.filter((event) => event.type === 'payment_completed' && event.transaction_id);
      const transactionEvents = [...new Map(paymentEvents.map((event) => [event.transaction_id!, event])).values()];
      const found = await Promise.all(transactionEvents.map(async (event): Promise<ReceiptRecord | null> => {
        const payloadReceipt = event.payload.receipt as Receipt | undefined;
        if (payloadReceipt?.status === 'paid' && payloadReceipt.transaction_id === event.transaction_id) return { receipt: payloadReceipt, quote: null, occurredAt: event.occurred_at };
        try { return { receipt: await api.paymentByTransaction(TOKEN, event.transaction_id!), quote: null, occurredAt: event.occurred_at }; }
        catch { return null; }
      }));
      const ordered = found.filter((record): record is ReceiptRecord => record !== null)
        .sort((a, b) => Date.parse(b.receipt.paid_at || b.occurredAt) - Date.parse(a.receipt.paid_at || a.occurredAt));
      setRecords((current) => {
        const previous = new Map(current.map((record) => [record.receipt.transaction_id, record]));
        return ordered.map((record) => {
          const cached = previous.get(record.receipt.transaction_id);
          return { ...record, quote: cached?.quote?.id === record.receipt.quote_id ? cached.quote : record.quote };
        });
      });
      loadedOnce.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load receipts.');
    } finally { inFlight.current = false; setLoading(false); }
  }, []);

  useEffect(() => { if (loaded && mandateId) void load(); }, [loaded, mandateId, load]);

  /** A payment completed in this session: show it at once and reload the history next time. */
  const remember = useCallback((receipt: Receipt, quote: Quote | null) => {
    const record: ReceiptRecord = { receipt, quote, occurredAt: receipt.paid_at };
    setRecords((current) => [record, ...current.filter((entry) => entry.receipt.transaction_id !== receipt.transaction_id)]
      .sort((a, b) => Date.parse(b.receipt.paid_at || b.occurredAt) - Date.parse(a.receipt.paid_at || a.occurredAt)));
    loadedOnce.current = false;
  }, []);

  const open = useCallback(async (record: ReceiptRecord) => {
    const requestId = ++detailRequestId.current;
    setSelected(record.receipt.id); setDetailError('');
    if (record.quote) { setDetailLoading(false); return; }
    setDetailLoading(true);
    try {
      const quote = await api.quoteById(TOKEN, record.receipt.quote_id);
      if (requestId === detailRequestId.current) setRecords((current) => current.map((entry) => entry.receipt.id === record.receipt.id ? { ...entry, quote } : entry));
    } catch (err) {
      if (requestId === detailRequestId.current) setDetailError(err instanceof Error ? err.message : 'Item details are unavailable right now.');
    } finally { if (requestId === detailRequestId.current) setDetailLoading(false); }
  }, []);

  const selectedRecord = useMemo(() => records.find((entry) => entry.receipt.id === selected) ?? null, [records, selected]);
  return { records, loading, error, load, remember, open, selected: selectedRecord, close: () => setSelected(null), detailLoading, detailError };
}

type Account = ReturnType<typeof useAccountState>;
const AccountContext = createContext<Account | null>(null);

export function AccountProvider({ children }: { children: ReactNode }) {
  const account = useAccountState();
  return <AccountContext.Provider value={account}>{children}</AccountContext.Provider>;
}

export function useAccount(): Account {
  const account = useContext(AccountContext);
  if (!account) throw new Error('useAccount needs AccountProvider');
  return account;
}
