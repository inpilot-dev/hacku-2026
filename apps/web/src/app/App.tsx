import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowDownRight, ArrowUpRight, BadgeCheck, Ban, ChevronDown, CircleHelp, Clock3, ExternalLink, Eye, FileCheck2, Leaf, LockKeyhole, Menu, MoreHorizontal, RefreshCw, Shield, ShieldAlert, ShoppingBasket, Sparkles, WalletCards, X } from 'lucide-react';
import type { AgentRun, AgentRunRequest, AuditEvent, AuditExport, BudgetResponse, CatalogResponse, DraftResponse, Evidence, Mandate, PaymentCompleted, Policy, Product, Quote, Receipt, RuleViolation, VerificationResult, VerifierResult } from '../../../../contracts/types';
import placeholderCatalog from '../../../../services/api/mandate/payments/fixtures/placeholder_catalog.json';
import { api, ApiError } from '../lib/api';
import { money, shortDate } from '../lib/format';

const DEFAULT_TOKEN = 'dev-user-token';
function clearIdempotency(operation: string) { sessionStorage.removeItem(`mandate-idempotency-${operation}`); }
const DRAFT_ID = 'draft_demo';
function hasVerifiedEvidenceReference(item: Evidence) {
  let source: URL;
  try { source = new URL(item.source_url); } catch { return false; }
  const conditions = item.conditions.trim();
  return ['http:', 'https:'].includes(source.protocol)
    && !source.username && !source.password
    && !source.hostname.endsWith('.invalid')
    && Boolean(item.capture_path?.trim())
    && Number.isFinite(Date.parse(item.observed_at))
    && Boolean(conditions)
    && !/(placeholder|not an observation|sample source|unverified)/i.test(conditions);
}

function hasSourceBackedCatalog(catalog: CatalogResponse) {
  const evidence = new Map(catalog.evidence.map((item) => [item.id, item]));
  const availableProducts = catalog.products.filter((product) => product.available);
  return availableProducts.length > 0 && availableProducts.every((product) => product.evidence_ids.some((id) => {
    const item = evidence.get(id);
    return Boolean(item && item.kind === 'product_price' && hasVerifiedEvidenceReference(item));
  }));
}
const initialPolicy = {
  currency: 'HKD' as const,
  per_order_limit_minor: 30000,
  period_limits: [{ period: 'calendar_week' as const, limit_minor: 80000, timezone: 'Asia/Hong_Kong' as const }],
  allowed_merchant_ids: ['demo_store_a'],
  blocked_categories: ['alcohol' as const],
  expires_at: '2026-10-31T23:59:59+08:00',
  approval_above_minor: null,
};

type View = 'overview' | 'shopping' | 'wallet' | 'activity';
type Toast = { title: string; detail: string; tone: 'success' | 'error' | 'neutral' };
type RecoveredPayment = { receipt: Receipt; replayed: true; recovered: true };
type PurchaseRefusal = { message: string; violations: RuleViolation[]; status: 'refused' | 'requires_review' };

function readStoredPurchaseRefusal(quoteId: string): PurchaseRefusal | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(`mandate-refusal-${quoteId}`) ?? 'null') as Partial<PurchaseRefusal> | null;
    if (!value || typeof value.message !== 'string' || !Array.isArray(value.violations) || !['refused', 'requires_review'].includes(value.status ?? '')) return null;
    return value as PurchaseRefusal;
  } catch { return null; }
}

function App() {
  const [view, setView] = useState<View>('overview');
  const [mobileNav, setMobileNav] = useState(false);
  const [token, setToken] = useState(() => sessionStorage.getItem('mandate.userToken.v1') ?? DEFAULT_TOKEN);
  const [mandateId, setMandateId] = useState(() => localStorage.getItem('mandate-id') ?? '');
  const [mandate, setMandate] = useState<Mandate | null>(null);
  const [budget, setBudget] = useState<BudgetResponse | null>(null);
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [catalogIsPlaceholder, setCatalogIsPlaceholder] = useState(true);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [paymentResult, setPaymentResult] = useState<(PaymentCompleted | RecoveredPayment) | null>(null);
  const [checkoutUncertain, setCheckoutUncertain] = useState(false);
  const [purchaseRefusal, setPurchaseRefusal] = useState<PurchaseRefusal | null>(null);
  const [health, setHealth] = useState<'checking' | 'online' | 'offline'>('checking');
  const [lastSuccessfulRefresh, setLastSuccessfulRefresh] = useState<string | null>(null);
  const refreshVersion = useRef(0);
  const catalogRequestVersion = useRef(0);
  const [busy, setBusy] = useState('');
  const [toast, setToast] = useState<Toast | null>(null);
  const [quantity, setQuantity] = useState<Record<string, number>>({ p_a_apples: 1, p_a_milk: 1, p_a_eggs: 1 });
  const [showSettings, setShowSettings] = useState(false);
  const [showMandateReview, setShowMandateReview] = useState(false);
  const [policy, setPolicy] = useState<Policy>(initialPolicy);
  const [quoteRestoreReady, setQuoteRestoreReady] = useState(false);
  const acceptAgentQuote = useCallback((nextQuote: Quote | null) => {
    setQuote(nextQuote);
    setPaymentResult(null);
    setPurchaseRefusal(null);
    setCheckoutUncertain(false);
  }, []);

  const announce = useCallback((next: Toast) => {
    setToast(next);
    window.setTimeout(() => setToast((current) => current === next ? null : current), 4500);
  }, []);

  const loadCatalog = useCallback(async () => {
    const version = ++catalogRequestVersion.current;
    try {
      const result = await api.catalog(token, 'demo_store_a');
      if (version !== catalogRequestVersion.current) return;
      setCatalog(result);
      setCatalogIsPlaceholder(!hasSourceBackedCatalog(result));
    } catch {
      if (version !== catalogRequestVersion.current) return;
      const fallback = placeholderCatalog as CatalogResponse;
      setCatalog(fallback);
      setCatalogIsPlaceholder(true);
    }
  }, [token]);

  useEffect(() => {
    if (!mandateId) { setCheckoutUncertain(false); setQuoteRestoreReady(true); return; }
    let cancelled = false;
    const savedQuoteId = sessionStorage.getItem(`mandate-pending-quote-${mandateId}`);
    if (!savedQuoteId) { setCheckoutUncertain(false); setQuoteRestoreReady(true); return; }
    setQuoteRestoreReady(false);
    setBusy('quote-restore');
    void (async () => {
      try {
        const savedQuote = await api.quoteById(token, savedQuoteId);
        if (cancelled) return;
        setQuote(savedQuote);
        const transactionId = sessionStorage.getItem(`mandate-tx-${savedQuote.id}`);
        if (transactionId) {
          setCheckoutUncertain(true);
          try {
            const receipt = await api.paymentByTransaction(token, transactionId);
            if (cancelled) return;
            setPaymentResult({ receipt, replayed: true, recovered: true });
            setPurchaseRefusal(null);
            setCheckoutUncertain(false);
            announce({ title: 'Previous checkout restored', detail: 'The wallet found the saved receipt. No new payment was submitted.', tone: 'success' });
          } catch {
            if (cancelled) return;
            const refusal = readStoredPurchaseRefusal(savedQuote.id);
            if (refusal) { setPurchaseRefusal(refusal); setCheckoutUncertain(false); }
            /* Without a saved refusal, a missing receipt is not proof of failure. */
          }
        } else {
          const refusal = readStoredPurchaseRefusal(savedQuote.id);
          if (refusal) { setPurchaseRefusal(refusal); setCheckoutUncertain(false); }
          else setCheckoutUncertain(false);
        }
      } catch {
        if (!cancelled) {
          sessionStorage.removeItem(`mandate-pending-quote-${mandateId}`);
          announce({ title: 'Saved basket could not be restored', detail: 'The wallet no longer has this quote. Create a fresh quote before checkout.', tone: 'neutral' });
        }
      } finally {
        if (!cancelled) { setBusy(''); setQuoteRestoreReady(true); }
      }
    })();
    return () => { cancelled = true; };
  }, [announce, mandateId, token]);

  useEffect(() => {
    if (!mandateId || !quoteRestoreReady) return;
    const storageKey = `mandate-pending-quote-${mandateId}`;
    if (quote) sessionStorage.setItem(storageKey, quote.id);
    else sessionStorage.removeItem(storageKey);
  }, [mandateId, quote, quoteRestoreReady]);

  useEffect(() => {
    if (!quote) return;
    const storageKey = `mandate-refusal-${quote.id}`;
    if (purchaseRefusal) sessionStorage.setItem(storageKey, JSON.stringify(purchaseRefusal));
    else sessionStorage.removeItem(storageKey);
  }, [purchaseRefusal, quote]);

  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current;
    setHealth('checking');
    let healthSucceeded = false;
    try {
      await api.health();
      healthSucceeded = true;
      if (version === refreshVersion.current) setHealth('online');
    } catch { if (version === refreshVersion.current) setHealth('offline'); }
    if (version !== refreshVersion.current) return;
    if (!token || !mandateId) {
      if (healthSucceeded) setLastSuccessfulRefresh(new Date().toISOString());
      return;
    }
    try {
      const [m, b] = await Promise.all([api.mandate(token, mandateId), api.budget(token, mandateId)]);
      if (version !== refreshVersion.current) return;
      setMandate(m);
      setBudget(b);
      setLastSuccessfulRefresh(new Date().toISOString());
    } catch (error) {
      if (version !== refreshVersion.current) return;
      if (error instanceof ApiError && error.status === 404) {
        setMandate(null); setBudget(null); setMandateId(''); localStorage.removeItem('mandate-id');
      } else if (error instanceof ApiError && error.status === 401) {
        announce({ title: 'Sign-in needed', detail: 'Check the demo user token in settings.', tone: 'error' });
      }
    }
  }, [announce, mandateId, token]);

  useEffect(() => { void refresh(); void loadCatalog(); }, [refresh, loadCatalog]);
  useEffect(() => { sessionStorage.setItem('mandate.userToken.v1', token); }, [token]);
  useEffect(() => { if (mandateId) localStorage.setItem('mandate-id', mandateId); }, [mandateId]);

  const products = useMemo(() => catalog?.products.filter((product) => product.merchant_id === 'demo_store_a' && product.available) ?? [], [catalog]);
  const pickedCount = Object.values(quantity).reduce((sum, count) => sum + count, 0);
  const currentBudget = budget?.applicable_budgets[0];
  const spentRatio = currentBudget ? Math.min(100, Math.round((currentBudget.paid_minor / currentBudget.limit_minor) * 100)) : 0;
  const active = mandate?.status === 'active';

  async function confirmMandate(requestedPolicy: Policy = policy, draftId = DRAFT_ID): Promise<boolean> {
    setBusy('mandate');
    try {
      const result = await api.confirm(token, { draft_id: draftId, policy: requestedPolicy });
      clearIdempotency('confirm-draft-demo');
      setMandate(result); setMandateId(result.id); setQuote(null); setPaymentResult(null); setPurchaseRefusal(null); setCheckoutUncertain(false);
      setView('overview');
      setShowMandateReview(false);
      setPolicy(requestedPolicy);
      try { setBudget(await api.budget(token, result.id)); } catch { setBudget(null); }
      announce({ title: 'Spending rules are active', detail: 'The wallet has confirmed the weekly grocery mandate.', tone: 'success' });
      return true;
    } catch (error) {
      announce({ title: 'Could not confirm mandate', detail: error instanceof Error ? error.message : 'Please check the API and try again.', tone: 'error' });
      return false;
    } finally { setBusy(''); }
  }

  async function buildQuote() {
    setBusy('quote'); setQuote(null); setPaymentResult(null); setPurchaseRefusal(null); setCheckoutUncertain(false);
    try {
      if (!active || !mandate) throw new Error('Confirm an active spending mandate first.');
      const items = Object.entries(quantity).filter(([, count]) => count > 0).map(([product_id, count]) => ({ product_id, quantity: count }));
      if (!items.length) throw new Error('Add at least one item to your basket.');
      const result = await api.quote(token, { merchant_id: 'demo_store_a', delivery_context_id: 'ctx_a_standard', items });
      setQuote(result);
      announce({ title: 'Server quote ready', detail: `The wallet priced this basket at ${money(result.total_minor)}.`, tone: 'success' });
    } catch (error) {
      announce({ title: 'Quote unavailable', detail: error instanceof Error ? error.message : 'Could not price this basket.', tone: 'error' });
    } finally { setBusy(''); }
  }

  async function refreshQuote(previous: Quote) {
    setBusy('quote'); setPurchaseRefusal(null); setCheckoutUncertain(false);
    try {
      const fresh = await api.quote(token, {
        merchant_id: previous.merchant_id,
        delivery_context_id: previous.delivery_context_id,
        expected_revision: previous.revision,
        items: previous.items.map(({ product_id, quantity: count }) => ({ product_id, quantity: count })),
      });
      setQuote(fresh); setPaymentResult(null);
      announce({ title: 'Fresh wallet quote ready', detail: `The wallet repriced this basket at ${money(fresh.total_minor)}.`, tone: 'success' });
    } catch (error) {
      announce({ title: 'Could not refresh quote', detail: error instanceof Error ? error.message : 'Please try again.', tone: 'error' });
    } finally { setBusy(''); }
  }

  function editRefusedBasket(previous: Quote) {
    setQuantity(Object.fromEntries(previous.items.map((item) => [item.product_id, item.quantity])));
    setQuote(null);
    setPaymentResult(null);
    setPurchaseRefusal(null);
    setCheckoutUncertain(false);
  }

  async function completeDemoPurchase() {
    if (!mandate || !quote || paymentResult) return;
    setBusy('purchase');
    const keyName = `mandate-tx-${quote.id}`;
    const transactionId = sessionStorage.getItem(keyName) ?? crypto.randomUUID();
    sessionStorage.setItem(keyName, transactionId);
    try {
      const result = await api.demoPurchase(token, { mandate_id: mandate.id, quote_id: quote.id, transaction_id: transactionId });
      if (result.authorization.status !== 'approved') {
        setCheckoutUncertain(false);
        setPurchaseRefusal({ message: result.authorization.message, violations: result.authorization.violations, status: result.authorization.status });
        announce({ title: result.authorization.status === 'requires_review' ? 'Needs your review' : 'Purchase blocked by wallet', detail: result.authorization.message, tone: result.authorization.status === 'refused' ? 'error' : 'neutral' });
        try { setBudget(await api.budget(token, mandate.id)); } catch { /* Keep the authoritative refusal visible. */ }
        return;
      }
      if (!result.payment) {
        setCheckoutUncertain(true);
        announce({ title: 'Purchase still being reconciled', detail: 'The same transaction ID is saved. Retry to safely check its existing state.', tone: 'neutral' });
        return;
      }
      if (result.payment.status === 'completed') {
        setPaymentResult(result.payment);
        setCheckoutUncertain(false);
        try { setBudget(await api.budget(token, mandate.id)); } catch { /* Keep the authoritative receipt even if the follow-up refresh fails. */ }
        announce({ title: result.payment.replayed ? 'Existing receipt recovered' : 'Sandbox purchase complete', detail: `${money(result.payment.receipt.amount_minor)} simulated · no money moved.`, tone: 'success' });
      } else {
        setCheckoutUncertain(false);
        setPurchaseRefusal({ message: result.payment.message, violations: result.payment.violations, status: 'refused' });
        announce({ title: 'Payment refused by wallet', detail: result.payment.message, tone: 'error' });
        try { setBudget(await api.budget(token, mandate.id)); } catch { /* Keep the authoritative refusal visible. */ }
      }
    } catch (error) {
      try {
        const receipt = await api.paymentByTransaction(token, transactionId);
        setPaymentResult({ receipt, replayed: true, recovered: true });
        setPurchaseRefusal(null);
        setCheckoutUncertain(false);
        try { setBudget(await api.budget(token, mandate.id)); } catch { /* Keep the authoritative recovered receipt visible. */ }
        announce({ title: 'Existing receipt recovered', detail: `${money(receipt.amount_minor)} simulated · recovered by transaction lookup; no real funds moved.`, tone: 'success' });
      } catch (lookupError) {
        setCheckoutUncertain(true);
        const notFoundYet = lookupError instanceof ApiError && lookupError.status === 404;
        announce({ title: 'Checkout result remains uncertain', detail: `${error instanceof Error ? error.message : 'Request failed.'} ${notFoundYet ? 'No receipt is visible yet; this does not prove the checkout failed.' : 'The wallet receipt lookup also failed.'} Retry keeps the same transaction ID.`, tone: 'error' });
      }
    } finally { setBusy(''); }
  }

  async function reconcileCheckout(savedQuote: Quote) {
    const transactionId = sessionStorage.getItem(`mandate-tx-${savedQuote.id}`);
    if (!transactionId || !mandate) {
      announce({ title: 'No saved checkout to reconcile', detail: 'Start checkout once to create a stable transaction ID.', tone: 'neutral' });
      return;
    }
    setBusy('reconcile');
    try {
      const receipt = await api.paymentByTransaction(token, transactionId);
      setPaymentResult({ receipt, replayed: true, recovered: true });
      setPurchaseRefusal(null);
      setCheckoutUncertain(false);
      try { setBudget(await api.budget(token, mandate.id)); } catch { /* Preserve the recovered wallet receipt. */ }
      announce({ title: 'Existing receipt recovered', detail: `${money(receipt.amount_minor)} simulated · no new payment was submitted.`, tone: 'success' });
    } catch (error) {
      setCheckoutUncertain(true);
      const notFoundYet = error instanceof ApiError && error.status === 404;
      announce({ title: 'Checkout is still unconfirmed', detail: notFoundYet ? 'No receipt is visible yet. Keep this transaction ID and check again before starting a different order.' : 'The wallet could not confirm the receipt. Check again before starting a different order.', tone: 'neutral' });
    } finally { setBusy(''); }
  }

  async function revokeMandate() {
    if (!mandate) return;
    setBusy('revoke');
    try {
      const result = await api.revoke(token, mandate.id);
      clearIdempotency(`revoke-${mandate.id}`);
      setMandate(result.mandate); setQuote(null);
      try { setBudget(await api.budget(token, mandate.id)); } catch { setBudget(null); }
      announce({ title: 'Mandate revoked', detail: `${result.cancelled_reservation_ids.length} pending reservation(s) cancelled.`, tone: 'success' });
    } catch (error) {
      announce({ title: 'Revocation failed', detail: error instanceof Error ? error.message : 'Please retry after checking the current state.', tone: 'error' });
    } finally { setBusy(''); }
  }

  function changeQuantity(productId: string, delta: number) {
    setQuantity((current) => ({ ...current, [productId]: Math.max(0, Math.min(20, (current[productId] ?? 0) + delta)) }));
    setQuote(null);
    setPaymentResult(null);
    setPurchaseRefusal(null);
    setCheckoutUncertain(false);
  }

  const nav = [
    { id: 'overview' as const, label: 'Overview', icon: Activity },
    { id: 'shopping' as const, label: 'Shopping', icon: ShoppingBasket, count: pickedCount },
    { id: 'wallet' as const, label: 'Family wallet', icon: WalletCards },
    { id: 'activity' as const, label: 'Activity & safety', icon: Shield },
  ];

  return <div className="app-shell">
    <aside className={`sidebar ${mobileNav ? 'sidebar-open' : ''}`}>
      <div className="brand"><span className="brand-mark"><Leaf size={18} strokeWidth={2.4} /></span><span>mandate<span className="brand-period">.</span></span><button className="icon-button mobile-close" aria-label="Close menu" onClick={() => setMobileNav(false)}><X size={18} /></button></div>
      <div className="family-switch"><span className="family-avatar">M</span><span><strong>Family account</strong><small>Family account</small></span><ChevronDown size={16} /></div>
      <div className="nav-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{nav.map(({ id, label, icon: Icon, count }) => <button key={id} className={`nav-item ${view === id ? 'selected' : ''}`} onClick={() => { setView(id); setMobileNav(false); }}><Icon size={17} /><span>{label}</span>{id === 'shopping' && count ? <span className="nav-count">{count}</span> : null}</button>)}</nav>
      <div className="sidebar-bottom"><div className="help-card"><span className="help-icon"><CircleHelp size={16} /></span><div><strong>Need a hand?</strong><small>See how Mandate works</small></div><ExternalLink size={14} /></div><button className="profile-row" onClick={() => setShowSettings(true)}><span className="profile-avatar">MK</span><span><strong>Minchan Kim</strong><small>Demo account</small></span><MoreHorizontal size={18} /></button></div>
    </aside>
    {mobileNav && <button className="scrim" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}

    <main className="main-area">
      <header className="topbar"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Menu size={20} /></button><div className="breadcrumbs">Family account <span>/</span> <strong>{nav.find((item) => item.id === view)?.label}</strong></div><div className="top-actions"><span className={`connection-pill ${health}`}><span className="connection-dot" />{health === 'online' ? 'Wallet connected' : health === 'offline' ? 'Wallet offline' : 'Connecting'}</span><span className="refresh-meta">{lastSuccessfulRefresh ? `Updated ${new Date(lastSuccessfulRefresh).toLocaleTimeString('en-HK', { hour: 'numeric', minute: '2-digit' })}` : 'Not refreshed yet'}</span><button className="icon-button" aria-label="Reconnect and refresh wallet data" title="Reconnect and refresh" onClick={() => void refresh()}><RefreshCw size={17} /></button><button className="top-avatar" aria-label="Account settings" onClick={() => setShowSettings(true)}>MK</button></div></header>
      <div className="page-content">
        {catalogIsPlaceholder && <div className="dev-banner"><span className="banner-icon"><ShieldAlert size={16} /></span><span><strong>Unverified catalog:</strong> product prices lack complete source evidence and are not verified live offers. Payments are simulated and no money moves.</span><button onClick={() => setView('activity')}>What this means <ArrowUpRight size={14} /></button></div>}
        {view === 'overview' && <Overview token={token} mandate={mandate} active={Boolean(active)} spentRatio={spentRatio} currentBudget={currentBudget} onShop={() => setView('shopping')} onSetup={() => setShowMandateReview(true)} onWallet={() => setView('wallet')} busy={busy} onRevoke={() => void revokeMandate()} />}
        {view === 'shopping' && <Shopping token={token} mandateId={mandate?.id ?? ''} mandateStatus={mandate?.status ?? null} products={products} evidence={catalog?.evidence ?? []} quantities={quantity} onChange={changeQuantity} quote={quote} onQuoteChange={acceptAgentQuote} busy={busy} active={Boolean(active)} onConfirm={() => mandate ? setView('wallet') : setShowMandateReview(true)} onBuildQuote={() => void buildQuote()} onRefreshQuote={(oldQuote) => void refreshQuote(oldQuote)} onEditRefusedBasket={editRefusedBasket} onPurchase={() => void completeDemoPurchase()} onReconcile={(savedQuote) => void reconcileCheckout(savedQuote)} paymentResult={paymentResult} checkoutUncertain={checkoutUncertain} purchaseRefusal={purchaseRefusal} onUnavailable={() => announce({ title: 'Checkout not connected yet', detail: 'The shopping worker is not in this checkout. This screen does not expose agent credentials or fake a completed purchase.', tone: 'neutral' })} catalogIsPlaceholder={catalogIsPlaceholder} />}
        {view === 'wallet' && <WalletView mandate={mandate} budget={budget} currentBudget={currentBudget} spentRatio={spentRatio} busy={busy} onRevoke={() => void revokeMandate()} onRefresh={() => void refresh()} />}
        {view === 'activity' && <SafetyView key={token} token={token} mandate={mandate} health={health} catalogIsPlaceholder={catalogIsPlaceholder} />}
      </div>
    </main>
    {showMandateReview && <MandateReviewModal token={token} initial={policy} busy={busy === 'mandate'} onClose={() => setShowMandateReview(false)} onConfirm={(next, draftId) => confirmMandate(next, draftId)} />}
    {showSettings && <Settings token={token} onToken={setToken} mandateId={mandateId} onMandateId={setMandateId} onClose={() => setShowSettings(false)} />}
    {toast && <div className={`toast toast-${toast.tone}`} role="status"><span>{toast.tone === 'success' ? <BadgeCheck size={19} /> : toast.tone === 'error' ? <ShieldAlert size={19} /> : <CircleHelp size={19} />}</span><div><strong>{toast.title}</strong><small>{toast.detail}</small></div><button className="icon-button" aria-label="Dismiss notification" onClick={() => setToast(null)}><X size={16} /></button></div>}
  </div>;
}

function Overview({ token, mandate, active, spentRatio, currentBudget, onShop, onSetup, onWallet, busy, onRevoke } : { token: string; mandate: Mandate | null; active: boolean; spentRatio: number; currentBudget?: BudgetResponse['applicable_budgets'][number]; onShop: () => void; onSetup: () => void; onWallet: () => void; busy: string; onRevoke: () => void }) {
  const [recentEvents, setRecentEvents] = useState<AuditEvent[]>([]);
  const [activityState, setActivityState] = useState<'loading' | 'connected' | 'unavailable' | 'error'>('loading');
  const [activityRefresh, setActivityRefresh] = useState(0);

  useEffect(() => {
    let disposed = false;
    let cursor = 0;
    let timer = 0;
    async function poll() {
      try {
        const page = await api.events(token, cursor, 50);
        if (disposed) return;
        cursor = page.next_after;
        setRecentEvents((current) => [...current, ...page.events].filter((event, index, all) => all.findIndex((item) => item.sequence === event.sequence && item.stream_id === event.stream_id) === index).slice(-50));
        setActivityState('connected');
      } catch (error) {
        if (disposed) return;
        setActivityState(error instanceof ApiError && error.status === 404 ? 'unavailable' : 'error');
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) return;
      }
      if (!disposed) timer = window.setTimeout(() => void poll(), 4000);
    }
    setActivityState('loading');
    void poll();
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [token, activityRefresh]);

  return <>
    <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-dot" /> YOUR FAMILY WALLET</div><h1>Everyday spending,<br className="mobile-break" /> with a little more peace of mind.</h1><p>Give someone you trust room to shop, with clear limits you stay in control of.</p></div><button className="button button-primary" onClick={active ? onShop : mandate ? onWallet : onSetup} disabled={busy === 'mandate'}>{busy === 'mandate' ? <span className="spinner" /> : active ? <ShoppingBasket size={17} /> : <Shield size={17} />}{active ? 'Start a grocery order' : mandate ? 'Review wallet status' : 'Set up family spending'}</button></div>
    <section className="summary-grid">
      <div className="summary-card balance-card"><div className="card-label"><span>AVAILABLE THIS WEEK</span><span className="icon-bubble green"><WalletCards size={17} /></span></div><strong className="balance-amount">{currentBudget ? money(currentBudget.available_minor) : '—'}</strong><div className="metric-foot"><span>{currentBudget ? `${money(currentBudget.paid_minor)} spent` : 'Confirm a mandate to activate a budget'}</span><span className="trend"><ArrowDownRight size={14} /> live wallet</span></div><div className="progress-track"><span style={{ width: `${spentRatio}%` }} /></div><div className="progress-meta"><span>{currentBudget ? `${spentRatio}% used` : 'No active allowance'}</span><span>{currentBudget ? `of ${money(currentBudget.limit_minor)}` : 'Weekly limit'}</span></div></div>
      <div className="summary-card"><div className="card-label"><span>ACTIVE PERMISSIONS</span><span className="icon-bubble lavender"><LockKeyhole size={17} /></span></div><strong className="stat-number">{active ? '01' : '00'}</strong><div className="metric-foot"><span>{active ? 'Weekly grocery allowance' : mandate?.status === 'revoked' ? 'Access revoked' : mandate?.status === 'expired' ? 'Allowance expired' : 'No spending rules yet'}</span><span className={`status-tag ${active ? 'status-green' : 'status-muted'}`}><i />{mandate?.status ?? 'Not set up'}</span></div></div>
      <div className="summary-card"><div className="card-label"><span>THIS WEEK</span><span className="icon-bubble peach"><ShoppingBasket size={17} /></span></div><strong className="stat-number">{currentBudget ? money(currentBudget.reserved_minor) : money(0)}</strong><div className="metric-foot"><span>Currently reserved</span><span className="plain-link">See wallet <ArrowUpRight size={14} /></span></div></div>
    </section>
    <section className="content-grid">
      <div className="panel mandate-panel"><div className="panel-heading"><div><div className="eyebrow">SPENDING RULES</div><h2>Family grocery allowance</h2></div><span className={`status-tag ${active ? 'status-green' : 'status-muted'}`}><i />{mandate?.status ?? 'Not set up'}</span></div>
        <MandateOverviewContent mandate={mandate} active={active} busy={busy} onSetup={onSetup} onWallet={onWallet} onRevoke={onRevoke} />
      </div>
      <div className="panel activity-panel"><div className="panel-heading"><div><div className="eyebrow">RECENT ACTIVITY</div><h2>Activity feed</h2></div><div className="feed-actions">{activityState !== 'loading' && <button className="text-button" onClick={() => { setActivityState('loading'); setActivityRefresh((value) => value + 1); }}>Refresh</button>}<span className={`status-tag ${activityState === 'connected' ? 'status-green' : 'status-muted'}`}><i />{activityState === 'connected' ? 'Live feed' : activityState === 'loading' ? 'Connecting' : activityState === 'unavailable' ? 'Not connected' : 'Unavailable'}</span></div></div>{recentEvents.length ? <div className="event-list">{[...recentEvents].reverse().slice(0, 5).map((event) => <div className="event-row" key={`${event.stream_id}-${event.sequence}`}><span className={`event-dot event-${event.type}`} /><span className="event-copy"><strong>{event.type.replace(/_/g, ' ')}</strong><small>Sequence {event.sequence} · {event.mandate_id ?? 'account'}{event.transaction_id ? ` · ${event.transaction_id}` : ''}</small></span><time>{new Date(event.occurred_at).toLocaleTimeString('en-HK', { hour: 'numeric', minute: '2-digit' })}</time></div>)}</div> : <div className="activity-empty"><span className="activity-empty-icon"><Sparkles size={19} /></span><strong>{activityState === 'unavailable' ? 'Activity feed not connected yet' : activityState === 'error' ? 'Could not load activity' : activityState === 'connected' ? 'No wallet activity yet' : 'Loading wallet activity…'}</strong><p>{activityState === 'unavailable' ? 'The event API is not available in this checkout.' : activityState === 'error' ? 'Check your user access or refresh to try again. No empty ledger is assumed.' : activityState === 'connected' ? 'Confirmed rules, quotes and wallet decisions will appear here.' : 'Fetching the latest events from your wallet.'}</p>{activityState === 'connected' && recentEvents.length === 0 && <button className="text-button" onClick={onShop}>Explore grocery shopping <ArrowUpRight size={14} /></button>}</div>}<div className="activity-security"><span className="secure-shield"><Shield size={16} /></span><span><strong>Your rules are checked at checkout</strong><small>The wallet, not the shopping assistant, decides whether a purchase can proceed.</small></span></div></div>
    </section>
    <div className="bottom-note"><span><Shield size={15} /> Your money stays in your own account.</span><span>Mandate is a prototype · <button onClick={() => {}}>How it works</button></span></div>
  </>;
}


function MandateOverviewContent({ mandate, active, busy, onSetup, onWallet, onRevoke }: { mandate: Mandate | null; active: boolean; busy: string; onSetup: () => void; onWallet: () => void; onRevoke: () => void }) {
  if (!mandate) return <div className="empty-state"><div className="empty-illustration"><Shield size={25} /></div><div><strong>No spending rules yet</strong><p>Set a weekly cap, choose a shop and decide what’s off limits. You can change or revoke access any time.</p><button className="text-button" onClick={onSetup} disabled={busy === 'mandate'}>{busy === 'mandate' ? 'Setting up…' : 'Create your first allowance'} <ArrowUpRight size={14} /></button></div></div>;
  return <><div className="rule-grid"><Rule icon={<WalletCards size={17} />} label="Per order" value={money(mandate.policy.per_order_limit_minor)} detail="Maximum basket total" /><Rule icon={<Activity size={17} />} label="Weekly budget" value={money(mandate.policy.period_limits[0]?.limit_minor ?? 0)} detail="Resets every Monday" /><Rule icon={<Ban size={17} />} label="Not allowed" value={mandate.policy.blocked_categories.join(', ')} detail="Blocked at checkout" /><Rule icon={<Clock3 size={17} />} label="Ends on" value={shortDate(mandate.policy.expires_at)} detail="Permission expiry" /></div>{active ? <div className="panel-footer"><div className="people-line"><span className="person-avatar caregiver">MK</span><span className="connector-line" /><span className="person-avatar delegate">A</span><span>You <span className="muted">authorize the assigned shopping agent</span></span></div><button className="text-button danger-text" onClick={onRevoke} disabled={busy === 'revoke'}>{busy === 'revoke' ? 'Revoking…' : 'Revoke access'}</button></div> : <div className="inactive-mandate"><AgentCharacter name="kip" state="revoked" label={mandate.status === 'expired' ? 'Kip marks this allowance expired' : 'Kip marks this allowance revoked'} /><div><strong>{mandate.status === 'expired' ? 'This allowance has expired' : 'This allowance was revoked'}</strong><p>The saved rules remain visible, but the wallet will not authorize spending with this mandate.</p><button className="text-button" onClick={onWallet}>{busy === 'mandate' ? 'Loading…' : 'Review wallet status'} <ArrowUpRight size={14} /></button></div></div>}</>;
}

function AgentCharacter({ name, state, label, size = 48 }: { name: 'bean' | 'kip' | 'kumi' | 'stella'; state: string; label: string; size?: number }) {
  return <img className="agent-character" src={`/agents/${name}-${state}.png`} alt={label} width={size} height={size} loading="lazy" />;
}

function Rule({ icon, label, value, detail }: { icon: React.ReactNode; label: string; value: string; detail: string }) { return <div className="rule-item"><span className="rule-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong><em>{detail}</em></span></div>; }

function Shopping({ token, mandateId, mandateStatus, products, evidence, quantities, onChange, quote, onQuoteChange, busy, active, onConfirm, onBuildQuote, onRefreshQuote, onEditRefusedBasket, onPurchase, onReconcile, paymentResult, checkoutUncertain, purchaseRefusal, onUnavailable, catalogIsPlaceholder }: { token: string; mandateId: string; mandateStatus: Mandate['status'] | null; products: Product[]; evidence: Evidence[]; quantities: Record<string, number>; onChange: (id: string, delta: number) => void; quote: Quote | null; onQuoteChange: (quote: Quote | null) => void; busy: string; active: boolean; onConfirm: () => void; onBuildQuote: () => void; onRefreshQuote: (quote: Quote) => void; onEditRefusedBasket: (quote: Quote) => void; onPurchase: () => void; onReconcile: (quote: Quote) => void; paymentResult: (PaymentCompleted | RecoveredPayment) | null; checkoutUncertain: boolean; purchaseRefusal: PurchaseRefusal | null; onUnavailable: () => void; catalogIsPlaceholder: boolean }) {
  const [shoppingListText, setShoppingListText] = useState('Apples\nMilk\nEggs');
  const [instruction, setInstruction] = useState('');
  const [agentRunId, setAgentRunId] = useState(() => mandateId ? sessionStorage.getItem(`mandate-agent-run-${mandateId}`) ?? '' : '');
  const [agentRun, setAgentRun] = useState<AgentRun | null>(null);
  const [agentSubmitting, setAgentSubmitting] = useState(false);
  const [agentError, setAgentError] = useState('');
  const [agentQuoteUnavailable, setAgentQuoteUnavailable] = useState(false);
  const [quoteRetry, setQuoteRetry] = useState(0);
  const [quoteNow, setQuoteNow] = useState(Date.now());
  const storageKey = mandateId ? `mandate-agent-run-${mandateId}` : '';
  const agentWorking = agentRun?.status === 'queued' || agentRun?.status === 'running';
  const basketCount = quote ? quote.items.reduce((sum, item) => sum + item.quantity, 0) : Object.values(quantities).reduce((sum, count) => sum + count, 0);
  const quoteExpired = Boolean(quote && Date.parse(quote.expires_at) <= quoteNow);

  useEffect(() => {
    if (!quote) return;
    const timer = window.setInterval(() => setQuoteNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [quote]);

  useEffect(() => {
    if (!storageKey) return;
    setAgentRunId(sessionStorage.getItem(storageKey) ?? '');
    setAgentRun(null);
  }, [storageKey]);

  useEffect(() => {
    if (!agentRunId || !mandateId) return;
    let disposed = false;
    let timer = 0;
    let loadedQuoteId = '';
    async function poll() {
      try {
        const latest = await api.agentRun(token, agentRunId);
        if (disposed) return;
        setAgentRun(latest);
        setAgentError('');
        if (latest.status === 'quoted' && latest.quote_id && latest.quote_id !== loadedQuoteId) {
          loadedQuoteId = latest.quote_id;
          try {
            onQuoteChange(await api.quoteById(token, latest.quote_id));
            if (!disposed) setAgentQuoteUnavailable(false);
          } catch (error) {
            if (!disposed) {
              setAgentQuoteUnavailable(true);
              setAgentError(`Run is ready, but its quote could not be loaded: ${error instanceof Error ? error.message : 'Request failed.'}`);
            }
          }
        }
        if (latest.status === 'queued' || latest.status === 'running') timer = window.setTimeout(() => void poll(), 1400);
      } catch (error) {
        if (disposed) return;
        const status = error instanceof ApiError ? error.status : 0;
        setAgentError([404, 405].includes(status) ? 'Agent-run service is not connected in this checkout. You can still build a quote from the catalog below.' : error instanceof Error ? error.message : 'Could not refresh agent progress.');
        if (![401, 403, 404, 405].includes(status)) timer = window.setTimeout(() => void poll(), 4000);
      }
    }
    void poll();
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [agentRunId, mandateId, onQuoteChange, quoteRetry, token]);

  async function startAgentRun(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active || !mandateId || agentSubmitting || agentWorking || busy) return;
    const shoppingList = shoppingListText.split(/\r?\n/).map((name) => name.trim()).filter(Boolean).map((name) => ({ name, quantity: 1 }));
    if (shoppingList.length === 0) { setAgentError('Add at least one item, one per line.'); return; }
    const request: AgentRunRequest = { mandate_id: mandateId, shopping_list: shoppingList, instruction: instruction.trim() || null, auto_purchase: false };
    setAgentError(''); setAgentQuoteUnavailable(false); setAgentRun(null); setAgentRunId(''); onQuoteChange(null);
    if (storageKey) sessionStorage.removeItem(storageKey);
    setAgentSubmitting(true);
    try {
      const started = await api.startAgentRun(token, request);
      clearIdempotency('agent-run');
      if (storageKey) sessionStorage.setItem(storageKey, started.id);
      setAgentRun(started);
      setAgentRunId(started.id);
    } catch (error) {
      setAgentError(error instanceof ApiError && [404, 405].includes(error.status)
        ? 'Agent-run service is not connected in this checkout. You can still build a quote from the catalog below.'
        : error instanceof Error ? error.message : 'Could not start the shopping agent.');
    } finally { setAgentSubmitting(false); }
  }

  return <>
    <div className="page-heading shopping-heading"><div><div className="eyebrow"><span className="eyebrow-dot" /> WEEKLY SHOP</div><h1>What’s on your list?</h1><p>Ask the agent to build a basket or choose catalog items yourself. The wallet still checks the final quote against your rules.</p></div><span className="merchant-chip"><span className="merchant-logo">D</span><span><strong>Demo Grocery Store A</strong><small>Currently supported merchant</small></span><ChevronDown size={15} /></span></div>
    {!active && <div className="inline-alert">{mandateStatus && <AgentCharacter name="kip" state="revoked" label={mandateStatus === 'expired' ? 'Kip shows the allowance has expired' : 'Kip shows spending access has been revoked'} size={40} />}<span><strong>{mandateStatus === 'expired' ? 'This allowance has expired.' : mandateStatus === 'revoked' ? 'Spending access was revoked.' : 'Set up your allowance before shopping.'}</strong> The wallet needs an active allowance before it can create an agent run or quote.</span><button className="text-button" onClick={onConfirm}>{mandateStatus ? 'Review wallet status' : 'Set up now'} <ArrowUpRight size={14} /></button></div>}
    <form className="panel agent-request-panel" onSubmit={(event) => void startAgentRun(event)}>
      <div className="agent-request-heading"><AgentCharacter name="kumi" state={paymentResult ? 'happy' : agentRun?.status === 'failed' || agentRun?.status === 'refused' ? 'sad' : 'idle'} label="Kumi, shopping agent" /><div><div className="eyebrow">SHOPPING AGENT</div><h2>Build a basket from your list</h2></div></div>
      <label>Items <small>One item per line; each starts at quantity 1.</small><textarea value={shoppingListText} onChange={(event) => setShoppingListText(event.target.value)} rows={3} disabled={!active || agentSubmitting || agentWorking} /></label>
      <label>Extra instruction <small>Optional preference for the agent; confirmed spending rules still take priority.</small><input value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="e.g. Prefer lower-sugar options" disabled={!active || agentSubmitting || agentWorking} /></label>
      <div className="agent-request-footer"><span><Shield size={14} /> Auto-purchase is off. Review the returned quote before checkout.</span><button className="button button-primary" type="submit" disabled={!active || agentSubmitting || agentWorking || Boolean(busy) || !shoppingListText.trim()}>{agentSubmitting || agentWorking ? <span className="spinner" /> : <Sparkles size={15} />}{agentSubmitting ? 'Starting agent…' : agentWorking ? 'Building basket…' : 'Ask agent to build basket'}</button></div>
      {agentRun && <div className={`agent-run-status agent-run-${agentRun.status}`} role="status"><strong>{agentRun.status.replace(/_/g, ' ')}</strong><span>{agentRun.message}</span><small>{agentRun.provider} · {agentRun.execution_mode}{agentRun.model_id ? ` · ${agentRun.model_id}` : ''}</small></div>}
      {agentError && <div className="form-error" role="alert">{agentError}{agentQuoteUnavailable && <button type="button" className="text-button" onClick={() => { setAgentError(''); setQuoteRetry((value) => value + 1); }}>Retry loading quote</button>}</div>}
    </form>
    <div className="shopping-layout"><section className="panel product-panel"><div className="panel-heading"><div><div className="eyebrow">SCRIPTED CATALOG FALLBACK</div><h2>Choose items manually</h2></div><span className="catalog-count">{products.length} items</span></div><div className="product-list">{products.map((product, index) => <ProductRow key={product.id} product={product} evidence={evidence} quantity={quantities[product.id] ?? 0} onChange={onChange} index={index} disabled={agentWorking || busy === 'quote' || busy === 'quote-restore' || busy === 'purchase'} />)}</div><div className="product-footnote"><span><LockKeyhole size={14} />{catalogIsPlaceholder ? 'Unverified catalog price · wallet calculates the final quote.' : 'Captured price evidence linked below each listing.'}</span><button className="text-button" onClick={onUnavailable}>See available stores <ArrowUpRight size={14} /></button></div></section>
      <aside className="panel basket-panel"><div className="panel-heading"><div><div className="eyebrow">YOUR BASKET</div><h2>Order summary</h2></div><span className="basket-badge"><ShoppingBasket size={14} />{basketCount}</span></div>{quote ? <>{checkoutUncertain && !paymentResult && <div className="checkout-uncertain" role="status"><AgentCharacter name="kip" state="idle" label="Kip is waiting for the wallet to confirm this transaction" /><strong>Checkout result is uncertain</strong><p>Check whether this transaction already produced a receipt before retrying.</p><button type="button" className="text-button" onClick={() => onReconcile(quote)} disabled={busy === 'reconcile'}>{busy === 'reconcile' ? 'Checking receipt…' : 'Check saved transaction'}</button></div>}{purchaseRefusal && <div className="purchase-refusal" role="alert"><AgentCharacter name="kip" state="refused" label="Kip blocked this purchase under the wallet rules" /><strong>{purchaseRefusal.status === 'requires_review' ? 'This purchase needs your review' : 'Wallet refused this purchase'}</strong><p>{purchaseRefusal.message}</p><ul>{purchaseRefusal.violations.map((violation, index) => <li key={`${violation.rule_id}-${index}`}><b>{violation.code.replace(/_/g, ' ')}</b> · {violation.message}<small>Rule {violation.rule_id}{violation.actual_minor != null ? ` · Actual ${money(violation.actual_minor)}` : ''}{violation.limit_minor != null ? ` · Limit ${money(violation.limit_minor)}` : ''}</small></li>)}</ul><button type="button" className="text-button" onClick={() => onEditRefusedBasket(quote)}>Adjust items and request a new quote</button></div>}{paymentResult && <div className="purchase-receipt"><AgentCharacter name="kip" state="approved" label="Kip approved the wallet-authorized sandbox purchase" size={40} /><span className="receipt-check"><BadgeCheck size={18} /></span><span><strong>Sandbox receipt saved</strong><small>{paymentResult.receipt.id} · {shortDate(paymentResult.receipt.paid_at)}</small></span><b>{money(paymentResult.receipt.amount_minor)}</b></div>}<div className={`quote-status${quoteExpired ? ' quote-expired' : ''}`}><BadgeCheck size={17} /><span><strong>{quoteExpired ? 'Quote expired' : 'Wallet quote ready'}</strong><small>{quoteExpired ? 'This quote can no longer be used to authorize a purchase.' : `Expires ${new Date(quote.expires_at).toLocaleTimeString('en-HK', { hour: 'numeric', minute: '2-digit' })}`}</small></span></div><div className="quote-lines">{quote.items.map((item) => <div className="quote-evidence-line" key={item.product_id}><span>{item.title} <small>×{item.quantity}</small><EvidenceRefs ids={item.evidence_ids} evidence={evidence} /></span><strong>{money(item.line_total_minor)}</strong></div>)}{quote.charges.length ? quote.charges.map((charge, index) => <div className="quote-evidence-line" key={`${charge.kind}-${index}`}><span>{charge.label}<EvidenceRefs ids={charge.evidence_ids} evidence={evidence} /></span><strong>{money(charge.amount_minor)}</strong></div>) : <div><span>Delivery</span><strong>{money(0)}</strong></div>}</div><div className="basket-total"><span>Total from wallet</span><strong>{money(quote.total_minor)}</strong></div>{quoteExpired && <button className="button button-secondary full-button" onClick={() => onRefreshQuote(quote)} disabled={busy === 'quote' || busy === 'purchase' || Boolean(paymentResult)}>{busy === 'quote' ? <span className="spinner" /> : <RefreshCw size={16} />}{busy === 'quote' ? 'Repricing basket…' : 'Request a fresh quote'}</button>}<button className="button button-primary full-button" onClick={onPurchase} disabled={!active || quoteExpired || busy === 'purchase' || busy === 'reconcile' || busy === 'quote-restore' || Boolean(paymentResult) || Boolean(purchaseRefusal)}>{busy === 'purchase' ? <span className="spinner" /> : <LockKeyhole size={16} />}{busy === 'purchase' ? 'Checking wallet…' : paymentResult ? 'Purchase complete' : purchaseRefusal ? 'Blocked by wallet' : !active ? mandateStatus === 'expired' ? 'Allowance expired' : mandateStatus === 'revoked' ? 'Access revoked' : 'Set up spending rules' : quoteExpired ? 'Quote expired' : checkoutUncertain ? 'Retry same transaction' : 'Complete sandbox purchase'}</button><p className="checkout-note">Wallet policy still decides · local demo payment · no real funds move.</p></> : <><div className="basket-empty"><div className="basket-art"><ShoppingBasket size={25} /></div><strong>Your basket is waiting</strong><p>Add items to see a quote from the wallet, including delivery.</p></div><div className="basket-estimate"><span>Estimated total</span><strong>{money(products.reduce((sum, product) => sum + product.unit_price_minor * (quantities[product.id] ?? 0), 0))}</strong></div><button className="button button-primary full-button" onClick={onBuildQuote} disabled={!active || busy === 'quote' || busy === 'quote-restore' || !Object.values(quantities).some((n) => n > 0)}>{busy === 'quote' || busy === 'quote-restore' ? <span className="spinner" /> : <Shield size={16} />}{busy === 'quote-restore' ? 'Restoring saved order…' : busy === 'quote' ? 'Getting wallet quote…' : 'Check against family rules'}</button><p className="checkout-note">{catalogIsPlaceholder ? 'Unverified prices · not live offer data' : 'Wallet checks the final basket total.'}</p></>}</aside></div>
    <div className="bottom-note"><span><Shield size={15} /> Shopping suggestions cannot authorize spending. The wallet is the final authority.</span><button className="text-button" onClick={onUnavailable}>How checkout works <ArrowUpRight size={14} /></button></div>
  </>;
}

function ProductRow({ product, evidence, quantity, onChange, index, disabled }: { product: Product; evidence: Evidence[]; quantity: number; onChange: (id: string, delta: number) => void; index: number; disabled: boolean }) {
  const emoji = ['🍎', '🥛', '🥚', '🍚', '🍺', '🍵', '🧺', '🧴', '🍞', '🍪'][index % 10];
  return <div className="product-row"><div className={`product-image product-image-${index % 5}`}><span>{emoji}</span></div><div className="product-info"><strong>{product.title}</strong><small>{product.description || product.unit_label}</small><span className="product-category">{product.category.replace(/_/g, ' ')}</span><EvidenceRefs ids={product.evidence_ids} evidence={evidence} /></div><div className="product-price"><strong>{money(product.unit_price_minor)}</strong><small>/{product.unit_label}</small></div><div className="quantity-control"><button aria-label={`Remove one ${product.title}`} onClick={() => onChange(product.id, -1)} disabled={disabled || quantity === 0}>−</button><span>{quantity}</span><button aria-label={`Add one ${product.title}`} onClick={() => onChange(product.id, 1)} disabled={disabled}>+</button></div></div>;
}

function EvidenceRefs({ ids, evidence }: { ids: string[]; evidence: Evidence[] }) {
  const references = ids.map((id) => evidence.find((item) => item.id === id)).filter((item): item is Evidence => Boolean(item));
  if (references.length === 0) return <small className="evidence-missing">No linked price source</small>;
  return <span className="evidence-refs">{references.map((item) => {
    let hostname = 'Unknown source';
    try { hostname = new URL(item.source_url).hostname; } catch { /* A malformed source is displayed as unverified text below. */ }
    const observed = Number.isFinite(Date.parse(item.observed_at)) ? new Date(item.observed_at).toLocaleDateString('en-HK', { month: 'short', day: 'numeric' }) : 'date missing';
    return !hasVerifiedEvidenceReference(item)
      ? <small className="evidence-unverified" key={item.id}>Source · not verified</small>
      : <a key={item.id} href={item.source_url} target="_blank" rel="noopener noreferrer" title={item.conditions}>{item.kind.replace(/_/g, ' ')} · {hostname} · {observed}</a>;
  })}</span>;
}

function WalletView({ mandate, budget, currentBudget, spentRatio, busy, onRevoke, onRefresh }: { mandate: Mandate | null; budget: BudgetResponse | null; currentBudget?: BudgetResponse['applicable_budgets'][number]; spentRatio: number; busy: string; onRevoke: () => void; onRefresh: () => void }) {
  return <><div className="page-heading"><div><div className="eyebrow">FAMILY WALLET</div><h1>Your rules, at a glance.</h1><p>Every amount reflects the latest state returned by the wallet service.</p></div><button className="button button-secondary" onClick={onRefresh}><RefreshCw size={16} /> Refresh wallet</button></div><section className="wallet-hero panel"><div><div className="eyebrow">AVAILABLE THIS WEEK</div><strong>{currentBudget ? money(currentBudget.available_minor) : '—'}</strong><span>{currentBudget ? `of ${money(currentBudget.limit_minor)} weekly allowance` : 'Confirm a mandate to activate your wallet'}</span><div className="progress-track"><span style={{ width: `${spentRatio}%` }} /></div><div className="wallet-breakdown"><span><i className="legend-dot paid-dot" />Paid <strong>{money(currentBudget?.paid_minor ?? 0)}</strong></span><span><i className="legend-dot reserved-dot" />Reserved <strong>{money(currentBudget?.reserved_minor ?? 0)}</strong></span><span><i className="legend-dot available-dot" />Available <strong>{money(currentBudget?.available_minor ?? 0)}</strong></span></div></div><div className="wallet-decoration"><WalletCards size={54} strokeWidth={1.1} /><span>FAMILY<br />ALLOWANCE</span><i>••••  2026</i></div></section><div className="wallet-detail-grid"><section className="panel"><div className="panel-heading"><div><div className="eyebrow">CURRENT PERMISSIONS</div><h2>Who can spend</h2></div><span className={`status-tag ${mandate?.status === 'active' ? 'status-green' : 'status-muted'}`}><i />{mandate?.status ?? 'Not set up'}</span></div>{mandate ? <div className="wallet-rule-list"><Rule icon={<WalletCards size={17} />} label="Per order limit" value={money(mandate.policy.per_order_limit_minor)} detail="Includes delivery fees" /><Rule icon={<Activity size={17} />} label="Weekly limit" value={money(mandate.policy.period_limits[0]?.limit_minor ?? 0)} detail="Calendar week · Hong Kong time" /><Rule icon={<Ban size={17} />} label="Blocked" value={mandate.policy.blocked_categories.join(', ')} detail="Always refused" /><Rule icon={<Clock3 size={17} />} label="Expires" value={shortDate(mandate.policy.expires_at)} detail="No automatic renewal" /></div> : <div className="empty-small">No active spending mandate has been confirmed yet.</div>}{mandate?.status === 'active' && <button className="button button-danger-outline" onClick={onRevoke} disabled={busy === 'revoke'}><Ban size={16} />{busy === 'revoke' ? 'Revoking access…' : 'Revoke spending access'}</button>}</section><section className="panel budget-panel"><div className="eyebrow">BUDGET PERIODS</div><h2>Spending by week</h2>{budget?.applicable_budgets.map((period) => <div className="budget-period" key={period.period_id}><div className="budget-period-head"><span><strong>{shortDate(period.starts_at)}</strong><small>Ends {shortDate(period.ends_at)}</small></span><strong>{money(period.available_minor)} <small>left</small></strong></div><div className="progress-track"><span style={{ width: `${Math.min(100, Math.round((period.paid_minor + period.reserved_minor) / period.limit_minor * 100))}%` }} /></div><div className="budget-period-meta"><span>{money(period.paid_minor + period.reserved_minor)} used</span><span>{money(period.limit_minor)} total</span></div></div>) ?? <div className="empty-small">Budget periods will appear after confirmation.</div>}</section></div></>;
}

function SafetyView({ token, mandate, health, catalogIsPlaceholder }: { token: string; mandate: Mandate | null; health: 'checking' | 'online' | 'offline'; catalogIsPlaceholder: boolean }) {
  const auditRequestVersion = useRef(0);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [eventsState, setEventsState] = useState<'loading' | 'connected' | 'unavailable' | 'error'>('loading');
  const [eventsRefresh, setEventsRefresh] = useState(0);
  const [audit, setAudit] = useState<AuditExport | null>(null);
  const [auditState, setAuditState] = useState<'loading' | 'connected' | 'unavailable' | 'error'>('loading');
  const [verifier, setVerifier] = useState<VerifierResult | null>(null);
  const [models, setModels] = useState<VerificationResult[]>([]);
  const [verifyBusy, setVerifyBusy] = useState<'audit' | 'model' | ''>('');
  const [actionError, setActionError] = useState('');

  useEffect(() => {
    let disposed = false;
    let cursor = 0;
    let timer = 0;
    async function poll() {
      try {
        const page = await api.events(token, cursor, 50);
        if (disposed) return;
        cursor = page.next_after;
        setEvents((current) => [...current, ...page.events].filter((event, index, all) => all.findIndex((item) => item.sequence === event.sequence && item.stream_id === event.stream_id) === index).slice(-100));
        setEventsState('connected');
      } catch (error) {
        if (disposed) return;
        setEventsState(error instanceof ApiError && error.status === 404 ? 'unavailable' : 'error');
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) return;
      }
      if (!disposed) timer = window.setTimeout(() => void poll(), 4000);
    }
    void poll();
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [token, eventsRefresh]);

  const loadAudit = useCallback(async () => {
    const version = ++auditRequestVersion.current;
    setAuditState('loading');
    try {
      const result = await api.auditExport(token);
      if (version !== auditRequestVersion.current) return;
      setAudit(result); setAuditState('connected');
    } catch (error) {
      if (version !== auditRequestVersion.current) return;
      setAudit(null); setAuditState(error instanceof ApiError && error.status === 404 ? 'unavailable' : 'error');
    }
  }, [token]);
  useEffect(() => { void loadAudit(); }, [loadAudit]);

  async function createCheckpoint() {
    if (!audit) return;
    setVerifyBusy('audit'); setActionError('');
    try {
      await api.createCheckpoint(token, { stream_id: audit.stream_id });
      clearIdempotency(`checkpoint-${audit.stream_id}`);
      await loadAudit();
    } catch (error) { setActionError(error instanceof Error ? error.message : 'Checkpoint request failed.'); }
    finally { setVerifyBusy(''); }
  }

  async function runAuditCheck() {
    if (!audit?.latest_checkpoint) {
      setActionError('There is no retained checkpoint to verify yet. No pass result is available.');
      return;
    }
    setVerifyBusy('audit'); setActionError('');
    try {
      setVerifier(await api.verifyAudit(token, { export: audit, retained_checkpoint_id: `${audit.stream_id}:${audit.latest_checkpoint.sequence}` }));
    } catch (error) { setActionError(error instanceof Error ? error.message : 'Verifier request failed.'); }
    finally { setVerifyBusy(''); }
  }

  async function runModelComparison() {
    setVerifyBusy('model'); setActionError(''); setModels([]);
    const shared = { initial_available_minor: 40000, purchase_amounts_minor: [30000, 30000], max_steps: 8, timeout_ms: 3000 };
    try {
      const results = await Promise.all([
        api.verifyModel(token, { ...shared, variant: 'unsafe' }),
        api.verifyModel(token, { ...shared, variant: 'atomic' }),
      ]);
      setModels(results);
    } catch (error) { setActionError(error instanceof Error ? error.message : 'Verification request failed.'); }
    finally { setVerifyBusy(''); }
  }

  const checkpointAvailable = Boolean(audit?.latest_checkpoint);
  const checks = [
    { name: 'Mandate is confirmed', state: mandate?.status === 'active', detail: mandate ? `Current state: ${mandate.status}` : 'No spending authority has been created.' },
    { name: 'Catalog evidence is source-backed', state: !catalogIsPlaceholder, detail: catalogIsPlaceholder ? 'Displayed products lack a valid price source, observation time or captured evidence.' : 'Every available product links to captured, timestamped price evidence.' },
    { name: 'Wallet API is reachable', state: health === 'online', detail: health === 'online' ? 'Connected to local sandbox wallet.' : health === 'checking' ? 'Checking wallet service…' : 'Wallet service is not responding.' },
    { name: 'Ordered event stream', state: eventsState === 'connected', detail: eventsState === 'connected' ? `${events.length} event(s) loaded; polling every 4 seconds.` : eventsState === 'unavailable' ? 'Events API is not connected in this checkout.' : eventsState === 'error' ? 'Events API request failed; check user access and server logs.' : 'Connecting to events API…' },
    { name: 'Checkpoint available for verification', state: auditState === 'connected' && checkpointAvailable, detail: auditState === 'connected' ? checkpointAvailable ? `Export includes checkpoint ${audit?.latest_checkpoint?.stream_id}:${audit?.latest_checkpoint?.sequence}; run the independent verifier to check it.` : 'Audit export is available, but it has no checkpoint.' : auditState === 'unavailable' ? 'Audit service is not connected in this checkout.' : auditState === 'error' ? 'Audit export request failed; check user access and server logs.' : 'Loading audit export…' },
    { name: 'Bounded concurrency model', state: models.length === 2, detail: models.length === 2 ? 'Unsafe and atomic variants returned from the configured solver.' : 'Run the model comparison to obtain current solver results.' },
  ];
  const eventLabel = (event: AuditEvent) => event.type.replace(/_/g, ' ');

  return <><div className="page-heading"><div><div className="eyebrow">TRANSPARENCY CENTER</div><h1>See what the system can prove.</h1><p>Mandate keeps authority, checkout and safety checks separate. Here’s what’s connected today.</p></div><span className="mode-chip"><span className="mode-dot" />LOCAL SANDBOX</span></div>
    <section className="panel safety-overview"><div className="safety-summary-icon"><Shield size={23} /></div><div><h2>Wallet-enforced permissions</h2><p>Shopping suggestions are not payment authority. The wallet checks the confirmed rules again when a purchase is requested.</p></div><span className="status-tag status-muted"><i />Wallet controls approval</span></section>
    <section className="panel checks-panel"><div className="panel-heading"><div><div className="eyebrow">INTEGRATION READINESS</div><h2>Connected services & evidence</h2></div><span className="checks-count">{checks.filter((item) => item.state).length} of {checks.length} ready</span></div><div className="check-list">{checks.map((item) => <div className="check-row" key={item.name}><span className={`check-state ${item.state ? 'check-ready' : 'check-pending'}`}>{item.state ? <BadgeCheck size={17} /> : <Clock3 size={16} />}</span><span><strong>{item.name}</strong><small>{item.detail}</small></span><span className={`check-label ${item.state ? 'ready-label' : ''}`}>{item.state ? 'Connected' : 'Not available'}</span></div>)}</div></section>
    <div className="safety-labs">
      <section className="panel lab-panel"><div className="panel-heading"><div><div className="eyebrow">AUDIT TRAIL</div><h2>Recent wallet events</h2></div><div className="feed-actions">{eventsState !== 'loading' && <button className="text-button" onClick={() => { setEventsState('loading'); setEventsRefresh((value) => value + 1); }}>Refresh</button>}<span className={`status-tag ${eventsState === 'connected' ? 'status-green' : 'status-muted'}`}><i />{eventsState === 'connected' ? 'Live feed' : eventsState === 'loading' ? 'Connecting' : eventsState === 'unavailable' ? 'Not connected' : 'Unavailable'}</span></div></div>
        {events.length ? <div className="event-list">{[...events].reverse().slice(0, 8).map((event) => <div className="event-row" key={`${event.stream_id}-${event.sequence}`}><span className={`event-dot event-${event.type}`} /><span className="event-copy"><strong>{eventLabel(event)}</strong><small>Sequence {event.sequence} · {event.mandate_id ?? 'account'}{event.transaction_id ? ` · ${event.transaction_id}` : ''}</small></span><time>{new Date(event.occurred_at).toLocaleTimeString('en-HK', { hour: 'numeric', minute: '2-digit' })}</time></div>)}</div> : <div className="lab-empty">{eventsState === 'unavailable' ? 'The event feed API is not connected yet.' : eventsState === 'error' ? 'Could not load events. The event feed does not assume an empty ledger.' : 'Loading authorized wallet events…'}</div>}
        {audit?.latest_checkpoint && <div className="checkpoint-row"><FileCheck2 size={15} /><span>Export checkpoint · {audit.latest_checkpoint.stream_id}:{audit.latest_checkpoint.sequence}</span><button className="text-button" onClick={() => void runAuditCheck()} disabled={verifyBusy !== ''}>{verifyBusy === 'audit' ? 'Checking…' : 'Verify history'}</button></div>}
        {auditState === 'connected' && !checkpointAvailable && <div className="checkpoint-row"><Clock3 size={15} /><span>No checkpoint exists for this export.</span><button className="text-button" onClick={() => void createCheckpoint()} disabled={verifyBusy !== ''}>{verifyBusy === 'audit' ? 'Creating…' : 'Create checkpoint'}</button></div>}
        {verifier && <div className={`result-banner ${verifier.valid ? 'result-good' : 'result-warn'}`}><AgentCharacter name="stella" state={verifier.valid ? 'pass' : 'fail'} label={verifier.valid ? 'Stella reports the audit check passed' : 'Stella reports the audit check failed'} size={40} /><strong>{verifier.status.replace(/_/g, ' ')}</strong><span>{verifier.message}</span><small>Checked through sequence {verifier.checked_through_sequence}; {verifier.unanchored_event_count} later event(s) unanchored.</small></div>}
      </section>
      <section className="panel lab-panel"><div className="panel-heading"><div><div className="eyebrow">SAFETY LAB</div><h2>Can two agents overspend?</h2></div><AgentCharacter name="stella" state={models.some((result) => result.status === 'counterexample_found') ? 'fail' : models.length === 2 && models.every((result) => result.status === 'no_counterexample_within_bound') ? 'pass' : 'idle'} label={models.some((result) => result.status === 'counterexample_found') ? 'Stella found a counterexample' : models.length === 2 && models.every((result) => result.status === 'no_counterexample_within_bound') ? 'Stella completed the bounded check' : 'Stella, checker'} /></div><p className="lab-description">Compare formal unsafe and atomic models under one HK$400 budget and two HK$300 requests. This runs the solver model; live competing wallet requests are a separate evaluation.</p><button className="button button-secondary lab-run" onClick={() => void runModelComparison()} disabled={verifyBusy !== ''}>{verifyBusy === 'model' ? <span className="spinner spinner-green" /> : <Activity size={15} />}{verifyBusy === 'model' ? 'Running bounded models…' : 'Run unsafe vs atomic models'}</button>
        {models.length > 0 && <div className="model-results">{models.map((result) => <div className="model-result" key={`${result.id}-${result.variant}`}><div><strong>{result.variant === 'unsafe' ? 'Unsafe' : 'Atomic reservation'}</strong><span className={`solver-tag ${result.status === 'inconclusive' ? 'solver-unknown' : result.status === 'counterexample_found' ? 'solver-bad' : 'solver-good'}`}>{result.status.replace(/_/g, ' ')}</span></div><p>{result.message}</p><small>{result.solver_result.toUpperCase()} · bound {result.max_steps} · {result.runtime_ms} ms</small>{result.counterexample.length > 0 && <div className="counterexample">{result.counterexample.map((step) => <div key={`${result.id}-${step.step}`}><b>{step.step}.</b> {step.actor}: {step.action}<small>{step.explanation}</small></div>)}</div>}</div>)}</div>}
        {actionError && <div className="form-error" role="alert">{actionError}</div>}
        <div className="model-limit"><CircleHelp size={14} /><span>Bounded result applies only to this model, bound and listed assumptions. It does not prove the deployed wallet correct.</span></div>
      </section>
    </div>
    <section className="honesty-grid"><div className="honesty-card"><span className="honesty-icon"><Eye size={18} /></span><strong>Catalog evidence</strong><p>{catalogIsPlaceholder ? 'Prices lack complete timestamped source and capture evidence; don’t present them as verified offers.' : 'Available products link to timestamped price sources and captures.'}</p></div><div className="honesty-card"><span className="honesty-icon"><WalletCards size={18} /></span><strong>Sandbox payment only</strong><p>No card is issued and no real funds move. A completed receipt is a local simulation.</p></div><div className="honesty-card"><span className="honesty-icon"><FileCheck2 size={18} /></span><strong>Evidence has limits</strong><p>An export without an independently retained checkpoint is not a verified audit history.</p></div></section>
    <div className="bottom-note"><span><Shield size={15} /> Live policy decisions come from the wallet service.</span><span>Prototype build · <button>Read safety notes</button></span></div></>;
}

function MandateReviewModal({ token, initial, busy, onClose, onConfirm }: { token: string; initial: Policy; busy: boolean; onClose: () => void; onConfirm: (policy: Policy, draftId: string) => Promise<boolean> }) {
  const [orderCap, setOrderCap] = useState(String(initial.per_order_limit_minor / 100));
  const [weeklyCap, setWeeklyCap] = useState(String(initial.period_limits[0] ? initial.period_limits[0].limit_minor / 100 : 800));
  const [expires, setExpires] = useState(initial.expires_at.slice(0, 10));
  const [blockAlcohol, setBlockAlcohol] = useState(initial.blocked_categories.includes('alcohol'));
  const [basePolicy, setBasePolicy] = useState(initial);
  const [draftText, setDraftText] = useState('Buy groceries each week. Spend no more than HK$300 per order and HK$800 per week. Only from Demo Grocery Store A. No alcohol. Permission expires 31 October 2026.');
  const [draftResponse, setDraftResponse] = useState<DraftResponse | null>(null);
  const [draftId, setDraftId] = useState(DRAFT_ID);
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftError, setDraftError] = useState('');
  const [error, setError] = useState('');
  const supportedPolicy = basePolicy.period_limits.length === 1 && basePolicy.period_limits[0].period === 'calendar_week' && basePolicy.period_limits[0].timezone === 'Asia/Hong_Kong' && basePolicy.allowed_merchant_ids.length === 1 && basePolicy.allowed_merchant_ids[0] === 'demo_store_a';

  function resetToSeededPolicy() {
    setDraftResponse(null); setDraftId(DRAFT_ID); setDraftError(''); setBasePolicy(initial);
    setOrderCap(String(initial.per_order_limit_minor / 100));
    setWeeklyCap(String(initial.period_limits[0] ? initial.period_limits[0].limit_minor / 100 : 800));
    setExpires(initial.expires_at.slice(0, 10));
    setBlockAlcohol(initial.blocked_categories.includes('alcohol'));
  }

  async function interpretRequest() {
    const text = draftText.trim();
    if (!text) { setDraftError('Describe the spending rules you want to review.'); return; }
    resetToSeededPolicy();
    setDraftBusy(true); setDraftError('');
    try {
      const result = await api.draft(token, { text, delegatee_id: 'agent_student' });
      clearIdempotency('mandate-draft');
      setDraftResponse(result); setDraftId(result.draft_id);
      if (result.proposed_policy) {
        const proposed = result.proposed_policy;
        setBasePolicy(proposed);
        setOrderCap(String(proposed.per_order_limit_minor / 100));
        setWeeklyCap(String(proposed.period_limits[0] ? proposed.period_limits[0].limit_minor / 100 : 0));
        setExpires(proposed.expires_at.slice(0, 10));
        setBlockAlcohol(proposed.blocked_categories.includes('alcohol'));
      }
    } catch (draftFailure) {
      resetToSeededPolicy();
      setDraftError(draftFailure instanceof ApiError && [404, 405].includes(draftFailure.status)
        ? 'Natural-language interpretation is not connected in this checkout. You can still set the structured rules below.'
        : draftFailure instanceof Error ? draftFailure.message : 'Could not interpret this request.');
    } finally { setDraftBusy(false); }
  }

  function editDraftText(text: string) {
    setDraftText(text);
    setDraftError('');
    if (!draftResponse) return;
    resetToSeededPolicy();
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const perOrder = Math.round(Number(orderCap) * 100);
    const weekly = Math.round(Number(weeklyCap) * 100);
    if (!Number.isFinite(perOrder) || perOrder < 1 || !Number.isFinite(weekly) || weekly < 1) { setError('Enter valid positive HKD spending limits.'); return; }
    if (!expires || new Date(`${expires}T23:59:59+08:00`) <= new Date()) { setError('Choose an expiry date in the future.'); return; }
    const blockedCategories: Policy['blocked_categories'] = basePolicy.blocked_categories.filter((category) => category !== 'alcohol');
    if (blockAlcohol) blockedCategories.push('alcohol');
    const next: Policy = { ...basePolicy, per_order_limit_minor: perOrder, period_limits: basePolicy.period_limits.map((period, index) => index === 0 ? { ...period, limit_minor: weekly } : period), expires_at: `${expires}T23:59:59+08:00`, blocked_categories: blockedCategories };
    setError('');
    await onConfirm(next, draftId);
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy && !draftBusy) onClose(); }}><section className="settings-modal mandate-modal" role="dialog" aria-modal="true" aria-labelledby="mandate-review-title"><div className="modal-heading"><span className="settings-icon"><Shield size={18} /></span><button className="icon-button" aria-label="Close mandate review" onClick={onClose} disabled={busy || draftBusy}><X size={18} /></button></div><div className="eyebrow">REVIEW BEFORE ACTIVATION</div><h2 id="mandate-review-title">Set the spending boundaries</h2><p>Interpretation is only a proposal. Check every rule and explicitly confirm it; the assistant cannot raise your limits later.</p>
    <section className="draft-interpreter" aria-label="Interpret a natural-language mandate"><label>Your request<textarea value={draftText} onChange={(event) => editDraftText(event.target.value)} rows={3} disabled={busy || draftBusy} /></label><button type="button" className="button button-secondary" onClick={() => void interpretRequest()} disabled={busy || draftBusy}>{draftBusy ? <span className="spinner spinner-green" /> : <Sparkles size={15} />}{draftBusy ? 'Interpreting request…' : 'Interpret request'}</button>
      {draftBusy && <div className="agent-inline"><AgentCharacter name="bean" state="thinking" label="Bean is interpreting the spending request" /><span>Bean is turning your words into a draft. You’ll review every rule before activation.</span></div>}
      {draftResponse && <div className="draft-result"><AgentCharacter name="bean" state="idle" label="Bean, mandate proposal ready" /><strong>Proposed by interpreter</strong><p>{draftResponse.summary}</p><small>Assigned agent: {draftResponse.delegatee_id} · Draft expires {shortDate(draftResponse.expires_at)}</small>{draftResponse.ambiguities.length > 0 && <div className="draft-ambiguities"><b>Clarify before confirming</b>{draftResponse.ambiguities.map((item) => <p key={`${item.field}-${item.question}`}><strong>{item.field}:</strong> {item.question}</p>)}</div>}</div>}
      {!draftResponse && <small className="draft-seed-note">Until interpretation returns, the text above is not applied; confirmation uses the structured rules and the local sample draft.</small>}
      {draftError && <div className="form-error" role="alert">{draftError}</div>}
    </section>
    <form onSubmit={(event) => void submit(event)}>
      <div className="limit-fields"><label>Max per order <span className="money-input"><i>HK$</i><input aria-label="Maximum per order in HKD" type="number" min="1" step="1" value={orderCap} onChange={(event) => setOrderCap(event.target.value)} /></span></label><label>Weekly limit <span className="money-input"><i>HK$</i><input aria-label="Weekly spending limit in HKD" type="number" min="1" step="1" value={weeklyCap} onChange={(event) => setWeeklyCap(event.target.value)} /></span></label></div>
      <label>Allowed store<input value={basePolicy.allowed_merchant_ids.map((id) => id === 'demo_store_a' ? 'Demo Grocery Store A' : id).join(', ') || 'No store specified'} readOnly /></label><label>Permission expires<input aria-label="Permission expiry date" type="date" value={expires} onChange={(event) => setExpires(event.target.value)} /></label>
      <label className="check-option"><input type="checkbox" checked={blockAlcohol} onChange={(event) => setBlockAlcohol(event.target.checked)} /><span><strong>Block alcohol</strong><small>Items in this category will be refused by the wallet.</small></span></label>
      <div className="draft-policy-details"><span>Budget periods: {basePolicy.period_limits.map((period) => `${period.period.replace(/_/g, ' ')} ${money(period.limit_minor)} · ${period.timezone}`).join('; ') || 'none'}</span><span>Other blocked categories: {basePolicy.blocked_categories.filter((category) => category !== 'alcohol').join(', ') || 'none'}</span><span>Extra approval above: {basePolicy.approval_above_minor === null ? 'none' : money(basePolicy.approval_above_minor)}</span></div>
      {!supportedPolicy && <div className="settings-note"><ShieldAlert size={16} /><span>This prototype can confirm one Asia/Hong_Kong weekly limit for Demo Grocery Store A. This proposal needs a compatible policy before it can be activated.</span></div>}
      <div className="settings-note"><ShieldAlert size={16} /><span>Prototype allowance for the local sandbox only. Confirming activates the exact structured rules shown here.</span></div>
      {error && <div className="form-error" role="alert">{error}</div>}
      <div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose} disabled={busy || draftBusy}>Cancel</button><button type="submit" className="button button-primary" disabled={busy || draftBusy || !supportedPolicy}>{busy ? <span className="spinner" /> : <LockKeyhole size={16} />}{busy ? 'Confirming…' : supportedPolicy ? 'Activate these rules' : 'Policy needs adjustment'}</button></div>
    </form></section></div>;
}

function Settings({ token, onToken, mandateId, onMandateId, onClose }: { token: string; onToken: (value: string) => void; mandateId: string; onMandateId: (value: string) => void; onClose: () => void }) {
  const [draftToken, setDraftToken] = useState(token);
  const [draftMandateId, setDraftMandateId] = useState(mandateId);
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title"><div className="modal-heading"><span className="settings-icon"><LockKeyhole size={18} /></span><button className="icon-button" aria-label="Close settings" onClick={onClose}><X size={18} /></button></div><div className="eyebrow">LOCAL DEMO SETTINGS</div><h2 id="settings-title">Connect to your wallet</h2><p>Use the scoped demo <em>user</em> token. Agent credentials never belong in this browser.</p><label>Demo user token<input value={draftToken} onChange={(event) => setDraftToken(event.target.value)} autoComplete="off" spellCheck={false} /></label><label>Mandate ID<input value={draftMandateId} onChange={(event) => setDraftMandateId(event.target.value)} placeholder="Set after confirming the sample mandate" autoComplete="off" /></label><div className="settings-note"><ShieldAlert size={16} /><span>This is prototype authentication. Don’t expose this local dev token on a shared network.</span></div><div className="modal-actions"><button className="button button-secondary" onClick={onClose}>Cancel</button><button className="button button-primary" onClick={() => { onToken(draftToken.trim()); onMandateId(draftMandateId.trim()); onClose(); }}>Save connection</button></div></section></div>;
}

export default App;
