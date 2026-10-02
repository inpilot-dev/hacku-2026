import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowDownRight, ArrowUpRight, BadgeCheck, Ban, ChevronDown, CircleHelp, Clock3, ExternalLink, Eye, FileCheck2, Leaf, LockKeyhole, Menu, MoreHorizontal, RefreshCw, Shield, ShieldAlert, ShoppingBasket, Sparkles, WalletCards, X } from 'lucide-react';
import type { AuditEvent, AuditExport, BudgetResponse, CatalogResponse, Mandate, PaymentCompleted, Policy, Product, Quote, VerificationResult, VerifierResult } from '../../../../contracts/types';
import placeholderCatalog from '../../../../services/api/mandate/payments/fixtures/placeholder_catalog.json';
import { api, ApiError } from '../lib/api';
import { money, shortDate } from '../lib/format';

const DEFAULT_TOKEN = 'dev-user-token';
function clearIdempotency(operation: string) { sessionStorage.removeItem(`mandate-idempotency-${operation}`); }
const DRAFT_ID = 'draft_demo';
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
  const [paymentResult, setPaymentResult] = useState<PaymentCompleted | null>(null);
  const [health, setHealth] = useState<'checking' | 'online' | 'offline'>('checking');
  const refreshVersion = useRef(0);
  const [busy, setBusy] = useState('');
  const [toast, setToast] = useState<Toast | null>(null);
  const [quantity, setQuantity] = useState<Record<string, number>>({ p_a_apples: 1, p_a_milk: 1, p_a_eggs: 1 });
  const [showSettings, setShowSettings] = useState(false);
  const [showMandateReview, setShowMandateReview] = useState(false);
  const [policy, setPolicy] = useState<Policy>(initialPolicy);

  const announce = useCallback((next: Toast) => {
    setToast(next);
    window.setTimeout(() => setToast((current) => current === next ? null : current), 4500);
  }, []);

  const loadCatalog = useCallback(async () => {
    try {
      const result = await api.catalog(token, 'demo_store_a');
      setCatalog(result);
      setCatalogIsPlaceholder(result.evidence.some((item) => item.source_url.includes('example.invalid')));
    } catch {
      const fallback = placeholderCatalog as CatalogResponse;
      setCatalog(fallback);
      setCatalogIsPlaceholder(true);
    }
  }, [token]);

  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current;
    setHealth('checking');
    try {
      await api.health();
      if (version === refreshVersion.current) setHealth('online');
    } catch { if (version === refreshVersion.current) setHealth('offline'); }
    if (!token || !mandateId || version !== refreshVersion.current) return;
    try {
      const [m, b] = await Promise.all([api.mandate(token, mandateId), api.budget(token, mandateId)]);
      if (version !== refreshVersion.current) return;
      setMandate(m);
      setBudget(b);
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

  async function confirmMandate(requestedPolicy: Policy = policy): Promise<boolean> {
    setBusy('mandate');
    try {
      const result = await api.confirm(token, { draft_id: DRAFT_ID, policy: requestedPolicy });
      clearIdempotency('confirm-draft-demo');
      setMandate(result); setMandateId(result.id); setQuote(null); setPaymentResult(null);
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
    setBusy('quote'); setQuote(null); setPaymentResult(null);
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

  async function completeDemoPurchase() {
    if (!mandate || !quote || paymentResult) return;
    setBusy('purchase');
    const keyName = `mandate-tx-${quote.id}`;
    const transactionId = sessionStorage.getItem(keyName) ?? crypto.randomUUID();
    sessionStorage.setItem(keyName, transactionId);
    try {
      const result = await api.demoPurchase(token, { mandate_id: mandate.id, quote_id: quote.id, transaction_id: transactionId });
      if (result.authorization.status !== 'approved') {
        setQuote(null);
        announce({ title: result.authorization.status === 'requires_review' ? 'Needs your review' : 'Purchase blocked by wallet', detail: result.authorization.message, tone: result.authorization.status === 'refused' ? 'error' : 'neutral' });
        try { setBudget(await api.budget(token, mandate.id)); } catch { /* Keep the authoritative refusal visible. */ }
        return;
      }
      if (!result.payment) {
        announce({ title: 'Purchase still being reconciled', detail: 'The same transaction ID is saved. Retry to safely check its existing state.', tone: 'neutral' });
        return;
      }
      if (result.payment.status === 'completed') {
        setPaymentResult(result.payment);
        sessionStorage.removeItem(keyName);
        try { setBudget(await api.budget(token, mandate.id)); } catch { /* Keep the authoritative receipt even if the follow-up refresh fails. */ }
        announce({ title: result.payment.replayed ? 'Existing receipt recovered' : 'Sandbox purchase complete', detail: `${money(result.payment.receipt.amount_minor)} simulated · no money moved.`, tone: 'success' });
      } else {
        setQuote(null);
        sessionStorage.removeItem(keyName);
        announce({ title: 'Payment refused by wallet', detail: result.payment.message, tone: 'error' });
        try { setBudget(await api.budget(token, mandate.id)); } catch { /* Keep the authoritative refusal visible. */ }
      }
    } catch (error) {
      announce({ title: 'Could not confirm checkout result', detail: `${error instanceof Error ? error.message : 'Request failed.'} Retry uses the same transaction.`, tone: 'error' });
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
      <header className="topbar"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Menu size={20} /></button><div className="breadcrumbs">Family account <span>/</span> <strong>{nav.find((item) => item.id === view)?.label}</strong></div><div className="top-actions"><span className={`connection-pill ${health}`}><span className="connection-dot" />{health === 'online' ? 'Wallet connected' : health === 'offline' ? 'Wallet offline' : 'Connecting'}</span><button className="icon-button" aria-label="Refresh data" onClick={() => void refresh()}><RefreshCw size={17} /></button><button className="top-avatar" aria-label="Account settings" onClick={() => setShowSettings(true)}>MK</button></div></header>
      <div className="page-content">
        {catalogIsPlaceholder && <div className="dev-banner"><span className="banner-icon"><ShieldAlert size={16} /></span><span><strong>Prototype data:</strong> product prices are placeholders, not live store offers. Payments are simulated and no money moves.</span><button onClick={() => setView('activity')}>What this means <ArrowUpRight size={14} /></button></div>}
        {view === 'overview' && <Overview token={token} mandate={mandate} active={Boolean(active)} spentRatio={spentRatio} currentBudget={currentBudget} onShop={() => setView('shopping')} onSetup={() => setShowMandateReview(true)} busy={busy} onRevoke={() => void revokeMandate()} />}
        {view === 'shopping' && <Shopping products={products} quantities={quantity} onChange={changeQuantity} quote={quote} busy={busy} active={Boolean(active)} onConfirm={() => setShowMandateReview(true)} onBuildQuote={() => void buildQuote()} onPurchase={() => void completeDemoPurchase()} paymentResult={paymentResult} onUnavailable={() => announce({ title: 'Checkout not connected yet', detail: 'The shopping worker is not in this checkout. This screen does not expose agent credentials or fake a completed purchase.', tone: 'neutral' })} catalogIsPlaceholder={catalogIsPlaceholder} />}
        {view === 'wallet' && <WalletView mandate={mandate} budget={budget} currentBudget={currentBudget} spentRatio={spentRatio} busy={busy} onRevoke={() => void revokeMandate()} onRefresh={() => void refresh()} />}
        {view === 'activity' && <SafetyView token={token} mandate={mandate} health={health} catalogIsPlaceholder={catalogIsPlaceholder} />}
      </div>
    </main>
    {showMandateReview && <MandateReviewModal initial={policy} busy={busy === 'mandate'} onClose={() => setShowMandateReview(false)} onConfirm={(next) => confirmMandate(next)} />}
    {showSettings && <Settings token={token} onToken={setToken} mandateId={mandateId} onMandateId={setMandateId} onClose={() => setShowSettings(false)} />}
    {toast && <div className={`toast toast-${toast.tone}`} role="status"><span>{toast.tone === 'success' ? <BadgeCheck size={19} /> : toast.tone === 'error' ? <ShieldAlert size={19} /> : <CircleHelp size={19} />}</span><div><strong>{toast.title}</strong><small>{toast.detail}</small></div><button className="icon-button" aria-label="Dismiss notification" onClick={() => setToast(null)}><X size={16} /></button></div>}
  </div>;
}

function Overview({ token, mandate, active, spentRatio, currentBudget, onShop, onSetup, busy, onRevoke }: { token: string; mandate: Mandate | null; active: boolean; spentRatio: number; currentBudget?: BudgetResponse['applicable_budgets'][number]; onShop: () => void; onSetup: () => void; busy: string; onRevoke: () => void }) {
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
    <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-dot" /> YOUR FAMILY WALLET</div><h1>Everyday spending,<br className="mobile-break" /> with a little more peace of mind.</h1><p>Give someone you trust room to shop, with clear limits you stay in control of.</p></div><button className="button button-primary" onClick={active ? onShop : onSetup} disabled={busy === 'mandate'}>{busy === 'mandate' ? <span className="spinner" /> : active ? <ShoppingBasket size={17} /> : <Shield size={17} />}{active ? 'Start a grocery order' : 'Set up family spending'}</button></div>
    <section className="summary-grid">
      <div className="summary-card balance-card"><div className="card-label"><span>AVAILABLE THIS WEEK</span><span className="icon-bubble green"><WalletCards size={17} /></span></div><strong className="balance-amount">{currentBudget ? money(currentBudget.available_minor) : '—'}</strong><div className="metric-foot"><span>{currentBudget ? `${money(currentBudget.paid_minor)} spent` : 'Confirm a mandate to activate a budget'}</span><span className="trend"><ArrowDownRight size={14} /> live wallet</span></div><div className="progress-track"><span style={{ width: `${spentRatio}%` }} /></div><div className="progress-meta"><span>{currentBudget ? `${spentRatio}% used` : 'No active allowance'}</span><span>{currentBudget ? `of ${money(currentBudget.limit_minor)}` : 'Weekly limit'}</span></div></div>
      <div className="summary-card"><div className="card-label"><span>ACTIVE PERMISSIONS</span><span className="icon-bubble lavender"><LockKeyhole size={17} /></span></div><strong className="stat-number">{active ? '01' : '00'}</strong><div className="metric-foot"><span>{active ? 'Weekly grocery allowance' : 'No spending rules yet'}</span><span className={`status-tag ${active ? 'status-green' : 'status-muted'}`}><i />{active ? 'Active' : 'Not set up'}</span></div></div>
      <div className="summary-card"><div className="card-label"><span>THIS WEEK</span><span className="icon-bubble peach"><ShoppingBasket size={17} /></span></div><strong className="stat-number">{currentBudget ? money(currentBudget.reserved_minor) : money(0)}</strong><div className="metric-foot"><span>Currently reserved</span><span className="plain-link">See wallet <ArrowUpRight size={14} /></span></div></div>
    </section>
    <section className="content-grid">
      <div className="panel mandate-panel"><div className="panel-heading"><div><div className="eyebrow">SPENDING RULES</div><h2>Family grocery allowance</h2></div><span className={`status-tag ${active ? 'status-green' : 'status-muted'}`}><i />{active ? 'Active' : 'Needs setup'}</span></div>
        {active && mandate ? <><div className="rule-grid"><Rule icon={<WalletCards size={17} />} label="Per order" value={money(mandate.policy.per_order_limit_minor)} detail="Maximum basket total" /><Rule icon={<Activity size={17} />} label="Weekly budget" value={money(mandate.policy.period_limits[0]?.limit_minor ?? 0)} detail="Resets every Monday" /><Rule icon={<Ban size={17} />} label="Not allowed" value={mandate.policy.blocked_categories.join(', ')} detail="Blocked at checkout" /><Rule icon={<Clock3 size={17} />} label="Ends on" value={shortDate(mandate.policy.expires_at)} detail="Permission expiry" /></div><div className="panel-footer"><div className="people-line"><span className="person-avatar caregiver">MK</span><span className="connector-line" /><span className="person-avatar delegate">A</span><span>You <span className="muted">authorize the assigned shopping agent</span></span></div><button className="text-button danger-text" onClick={onRevoke} disabled={busy === 'revoke'}>{busy === 'revoke' ? 'Revoking…' : 'Revoke access'}</button></div></> : <div className="empty-state"><div className="empty-illustration"><Shield size={25} /></div><div><strong>No spending rules yet</strong><p>Set a weekly cap, choose a shop and decide what’s off limits. You can change or revoke access any time.</p><button className="text-button" onClick={onSetup} disabled={busy === 'mandate'}>{busy === 'mandate' ? 'Setting up…' : 'Create your first allowance'} <ArrowUpRight size={14} /></button></div></div>}
      </div>
      <div className="panel activity-panel"><div className="panel-heading"><div><div className="eyebrow">RECENT ACTIVITY</div><h2>Activity feed</h2></div><div className="feed-actions">{activityState !== 'loading' && <button className="text-button" onClick={() => { setActivityState('loading'); setActivityRefresh((value) => value + 1); }}>Refresh</button>}<span className={`status-tag ${activityState === 'connected' ? 'status-green' : 'status-muted'}`}><i />{activityState === 'connected' ? 'Live feed' : activityState === 'loading' ? 'Connecting' : activityState === 'unavailable' ? 'Not connected' : 'Unavailable'}</span></div></div>{recentEvents.length ? <div className="event-list">{[...recentEvents].reverse().slice(0, 5).map((event) => <div className="event-row" key={`${event.stream_id}-${event.sequence}`}><span className={`event-dot event-${event.type}`} /><span className="event-copy"><strong>{event.type.replace(/_/g, ' ')}</strong><small>Sequence {event.sequence} · {event.mandate_id ?? 'account'}{event.transaction_id ? ` · ${event.transaction_id}` : ''}</small></span><time>{new Date(event.occurred_at).toLocaleTimeString('en-HK', { hour: 'numeric', minute: '2-digit' })}</time></div>)}</div> : <div className="activity-empty"><span className="activity-empty-icon"><Sparkles size={19} /></span><strong>{activityState === 'unavailable' ? 'Activity feed not connected yet' : activityState === 'error' ? 'Could not load activity' : activityState === 'connected' ? 'No wallet activity yet' : 'Loading wallet activity…'}</strong><p>{activityState === 'unavailable' ? 'The event API is not available in this checkout.' : activityState === 'error' ? 'Check your user access or refresh to try again. No empty ledger is assumed.' : activityState === 'connected' ? 'Confirmed rules, quotes and wallet decisions will appear here.' : 'Fetching the latest events from your wallet.'}</p>{activityState === 'connected' && recentEvents.length === 0 && <button className="text-button" onClick={onShop}>Explore grocery shopping <ArrowUpRight size={14} /></button>}</div>}<div className="activity-security"><span className="secure-shield"><Shield size={16} /></span><span><strong>Your rules are checked at checkout</strong><small>The wallet, not the shopping assistant, decides whether a purchase can proceed.</small></span></div></div>
    </section>
    <div className="bottom-note"><span><Shield size={15} /> Your money stays in your own account.</span><span>Mandate is a prototype · <button onClick={() => {}}>How it works</button></span></div>
  </>;
}

function Rule({ icon, label, value, detail }: { icon: React.ReactNode; label: string; value: string; detail: string }) { return <div className="rule-item"><span className="rule-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong><em>{detail}</em></span></div>; }

function Shopping({ products, quantities, onChange, quote, busy, active, onConfirm, onBuildQuote, onPurchase, paymentResult, onUnavailable, catalogIsPlaceholder }: { products: Product[]; quantities: Record<string, number>; onChange: (id: string, delta: number) => void; quote: Quote | null; busy: string; active: boolean; onConfirm: () => void; onBuildQuote: () => void; onPurchase: () => void; paymentResult: PaymentCompleted | null; onUnavailable: () => void; catalogIsPlaceholder: boolean }) {
  return <>
    <div className="page-heading shopping-heading"><div><div className="eyebrow"><span className="eyebrow-dot" /> WEEKLY SHOP</div><h1>What’s on your list?</h1><p>Pick a few essentials. The wallet will check the final quoted total against your family rules.</p></div><span className="merchant-chip"><span className="merchant-logo">D</span><span><strong>Demo Grocery Store A</strong><small>Prototype merchant · delivery</small></span><ChevronDown size={15} /></span></div>
    {!active && <div className="inline-alert"><ShieldAlert size={18} /><span><strong>Set up your allowance before shopping.</strong> The wallet needs active spending rules to create a quote.</span><button className="text-button" onClick={onConfirm}>Set up now <ArrowUpRight size={14} /></button></div>}
    <div className="shopping-layout"><section className="panel product-panel"><div className="panel-heading"><div><div className="eyebrow">DEMO CATALOG</div><h2>Everyday essentials</h2></div><span className="catalog-count">{products.length} items</span></div><div className="product-list">{products.map((product, index) => <ProductRow key={product.id} product={product} quantity={quantities[product.id] ?? 0} onChange={onChange} index={index} disabled={busy === 'quote' || busy === 'purchase'} />)}</div><div className="product-footnote"><span><LockKeyhole size={14} /> Wallet-priced product · prototype listing.</span><button className="text-button" onClick={onUnavailable}>See available stores <ArrowUpRight size={14} /></button></div></section>
      <aside className="panel basket-panel"><div className="panel-heading"><div><div className="eyebrow">YOUR BASKET</div><h2>Order summary</h2></div><span className="basket-badge"><ShoppingBasket size={14} />{Object.values(quantities).reduce((sum, count) => sum + count, 0)}</span></div>{quote ? <>{paymentResult && <div className="purchase-receipt"><span className="receipt-check"><BadgeCheck size={18} /></span><span><strong>Sandbox receipt saved</strong><small>{paymentResult.receipt.id} · {shortDate(paymentResult.receipt.paid_at)}</small></span><b>{money(paymentResult.receipt.amount_minor)}</b></div>}<div className="quote-status"><BadgeCheck size={17} /><span><strong>Wallet quote ready</strong><small>Expires {new Date(quote.expires_at).toLocaleTimeString('en-HK', { hour: 'numeric', minute: '2-digit' })}</small></span></div><div className="quote-lines">{quote.items.map((item) => <div key={item.product_id}><span>{item.title} <small>×{item.quantity}</small></span><strong>{money(item.line_total_minor)}</strong></div>)}<div><span>Delivery</span><strong>{money(quote.charges.reduce((sum, charge) => sum + charge.amount_minor, 0))}</strong></div></div><div className="basket-total"><span>Total from wallet</span><strong>{money(quote.total_minor)}</strong></div><button className="button button-primary full-button" onClick={onPurchase} disabled={busy === 'purchase' || Boolean(paymentResult)}>{busy === 'purchase' ? <span className="spinner" /> : <LockKeyhole size={16} />}{busy === 'purchase' ? 'Checking wallet…' : paymentResult ? 'Purchase complete' : 'Complete sandbox purchase'}</button><p className="checkout-note">Local scripted demo only · wallet policy still decides · no real funds move.</p></> : <><div className="basket-empty"><div className="basket-art"><ShoppingBasket size={25} /></div><strong>Your basket is waiting</strong><p>Add items to see a quote from the wallet, including delivery.</p></div><div className="basket-estimate"><span>Estimated total</span><strong>{money(products.reduce((sum, product) => sum + product.unit_price_minor * (quantities[product.id] ?? 0), 0))}</strong></div><button className="button button-primary full-button" onClick={onBuildQuote} disabled={!active || busy === 'quote' || !Object.values(quantities).some((n) => n > 0)}>{busy === 'quote' ? <span className="spinner" /> : <Shield size={16} />}{busy === 'quote' ? 'Getting wallet quote…' : 'Check against family rules'}</button><p className="checkout-note">{catalogIsPlaceholder ? 'Prototype prices only · no live offers' : 'Wallet checks the final basket total.'}</p></>}</aside></div>
    <div className="bottom-note"><span><Shield size={15} /> The shopping agent is not connected yet. The wallet remains the only authority for spending.</span><button className="text-button" onClick={onUnavailable}>How checkout works <ArrowUpRight size={14} /></button></div>
  </>;
}

function ProductRow({ product, quantity, onChange, index, disabled }: { product: Product; quantity: number; onChange: (id: string, delta: number) => void; index: number; disabled: boolean }) {
  const emoji = ['🍎', '🥛', '🥚', '🍚', '🍺', '🍵', '🧺', '🧴', '🍞', '🍪'][index % 10];
  return <div className="product-row"><div className={`product-image product-image-${index % 5}`}><span>{emoji}</span></div><div className="product-info"><strong>{product.title}</strong><small>{product.description || product.unit_label}</small><span className="product-category">{product.category.replace(/_/g, ' ')}</span></div><div className="product-price"><strong>{money(product.unit_price_minor)}</strong><small>/{product.unit_label}</small></div><div className="quantity-control"><button aria-label={`Remove one ${product.title}`} onClick={() => onChange(product.id, -1)} disabled={disabled || quantity === 0}>−</button><span>{quantity}</span><button aria-label={`Add one ${product.title}`} onClick={() => onChange(product.id, 1)} disabled={disabled}>+</button></div></div>;
}

function WalletView({ mandate, budget, currentBudget, spentRatio, busy, onRevoke, onRefresh }: { mandate: Mandate | null; budget: BudgetResponse | null; currentBudget?: BudgetResponse['applicable_budgets'][number]; spentRatio: number; busy: string; onRevoke: () => void; onRefresh: () => void }) {
  return <><div className="page-heading"><div><div className="eyebrow">FAMILY WALLET</div><h1>Your rules, at a glance.</h1><p>Every amount reflects the latest state returned by the wallet service.</p></div><button className="button button-secondary" onClick={onRefresh}><RefreshCw size={16} /> Refresh wallet</button></div><section className="wallet-hero panel"><div><div className="eyebrow">AVAILABLE THIS WEEK</div><strong>{currentBudget ? money(currentBudget.available_minor) : '—'}</strong><span>{currentBudget ? `of ${money(currentBudget.limit_minor)} weekly allowance` : 'Confirm a mandate to activate your wallet'}</span><div className="progress-track"><span style={{ width: `${spentRatio}%` }} /></div><div className="wallet-breakdown"><span><i className="legend-dot paid-dot" />Paid <strong>{money(currentBudget?.paid_minor ?? 0)}</strong></span><span><i className="legend-dot reserved-dot" />Reserved <strong>{money(currentBudget?.reserved_minor ?? 0)}</strong></span><span><i className="legend-dot available-dot" />Available <strong>{money(currentBudget?.available_minor ?? 0)}</strong></span></div></div><div className="wallet-decoration"><WalletCards size={54} strokeWidth={1.1} /><span>FAMILY<br />ALLOWANCE</span><i>••••  2026</i></div></section><div className="wallet-detail-grid"><section className="panel"><div className="panel-heading"><div><div className="eyebrow">CURRENT PERMISSIONS</div><h2>Who can spend</h2></div><span className={`status-tag ${mandate?.status === 'active' ? 'status-green' : 'status-muted'}`}><i />{mandate?.status ?? 'Not set up'}</span></div>{mandate ? <div className="wallet-rule-list"><Rule icon={<WalletCards size={17} />} label="Per order limit" value={money(mandate.policy.per_order_limit_minor)} detail="Includes delivery fees" /><Rule icon={<Activity size={17} />} label="Weekly limit" value={money(mandate.policy.period_limits[0]?.limit_minor ?? 0)} detail="Calendar week · Hong Kong time" /><Rule icon={<Ban size={17} />} label="Blocked" value={mandate.policy.blocked_categories.join(', ')} detail="Always refused" /><Rule icon={<Clock3 size={17} />} label="Expires" value={shortDate(mandate.policy.expires_at)} detail="No automatic renewal" /></div> : <div className="empty-small">No active spending mandate has been confirmed yet.</div>}{mandate?.status === 'active' && <button className="button button-danger-outline" onClick={onRevoke} disabled={busy === 'revoke'}><Ban size={16} />{busy === 'revoke' ? 'Revoking access…' : 'Revoke spending access'}</button>}</section><section className="panel budget-panel"><div className="eyebrow">BUDGET PERIODS</div><h2>Spending by week</h2>{budget?.applicable_budgets.map((period) => <div className="budget-period" key={period.period_id}><div className="budget-period-head"><span><strong>{shortDate(period.starts_at)}</strong><small>Ends {shortDate(period.ends_at)}</small></span><strong>{money(period.available_minor)} <small>left</small></strong></div><div className="progress-track"><span style={{ width: `${Math.min(100, Math.round((period.paid_minor + period.reserved_minor) / period.limit_minor * 100))}%` }} /></div><div className="budget-period-meta"><span>{money(period.paid_minor + period.reserved_minor)} used</span><span>{money(period.limit_minor)} total</span></div></div>) ?? <div className="empty-small">Budget periods will appear after confirmation.</div>}</section></div></>;
}

function SafetyView({ token, mandate, health, catalogIsPlaceholder }: { token: string; mandate: Mandate | null; health: 'checking' | 'online' | 'offline'; catalogIsPlaceholder: boolean }) {
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
    setAuditState('loading');
    try { setAudit(await api.auditExport(token)); setAuditState('connected'); }
    catch (error) { setAudit(null); setAuditState(error instanceof ApiError && error.status === 404 ? 'unavailable' : 'error'); }
  }, [token]);
  useEffect(() => { void loadAudit(); }, [loadAudit]);

  async function createCheckpoint() {
    if (!audit) return;
    setVerifyBusy('audit'); setActionError('');
    try {
      await api.createCheckpoint(token, { stream_id: audit.stream_id });
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
    { name: 'Catalog evidence is source-backed', state: !catalogIsPlaceholder, detail: catalogIsPlaceholder ? 'Prototype fixture uses example.invalid evidence.' : 'Catalog endpoint returned non-placeholder evidence.' },
    { name: 'Wallet API is reachable', state: health === 'online', detail: health === 'online' ? 'Connected to local sandbox wallet.' : health === 'checking' ? 'Checking wallet service…' : 'Wallet service is not responding.' },
    { name: 'Ordered event stream', state: eventsState === 'connected', detail: eventsState === 'connected' ? `${events.length} event(s) loaded; polling every 4 seconds.` : eventsState === 'unavailable' ? 'Events API is not connected in this checkout.' : eventsState === 'error' ? 'Events API request failed; check user access and server logs.' : 'Connecting to events API…' },
    { name: 'Independent audit checkpoint', state: auditState === 'connected' && checkpointAvailable, detail: auditState === 'connected' ? checkpointAvailable ? `Export includes checkpoint ${audit?.latest_checkpoint?.stream_id}:${audit?.latest_checkpoint?.sequence}; not yet independently checked.` : 'Audit export is available, but it has no checkpoint.' : auditState === 'unavailable' ? 'Audit service is not connected in this checkout.' : auditState === 'error' ? 'Audit export request failed; check user access and server logs.' : 'Loading audit export…' },
    { name: 'Bounded concurrency model', state: models.length === 2, detail: models.length === 2 ? 'Unsafe and atomic variants returned from the configured solver.' : 'Run the model comparison to obtain current solver results.' },
  ];
  const eventLabel = (event: AuditEvent) => event.type.replace(/_/g, ' ');

  return <><div className="page-heading"><div><div className="eyebrow">TRANSPARENCY CENTER</div><h1>See what the system can prove.</h1><p>Mandate keeps authority, checkout and safety checks separate. Here’s what’s connected today.</p></div><span className="mode-chip"><span className="mode-dot" />LOCAL SANDBOX</span></div>
    <section className="panel safety-overview"><div className="safety-summary-icon"><Shield size={23} /></div><div><h2>Wallet-enforced permissions</h2><p>Shopping suggestions are not payment authority. The wallet checks the confirmed rules again when a purchase is requested.</p></div><span className="status-tag status-green"><i />Policy checked at payment</span></section>
    <section className="panel checks-panel"><div className="panel-heading"><div><div className="eyebrow">INTEGRATION READINESS</div><h2>Connected services & evidence</h2></div><span className="checks-count">{checks.filter((item) => item.state).length} of {checks.length} ready</span></div><div className="check-list">{checks.map((item) => <div className="check-row" key={item.name}><span className={`check-state ${item.state ? 'check-ready' : 'check-pending'}`}>{item.state ? <BadgeCheck size={17} /> : <Clock3 size={16} />}</span><span><strong>{item.name}</strong><small>{item.detail}</small></span><span className={`check-label ${item.state ? 'ready-label' : ''}`}>{item.state ? 'Connected' : 'Not available'}</span></div>)}</div></section>
    <div className="safety-labs">
      <section className="panel lab-panel"><div className="panel-heading"><div><div className="eyebrow">AUDIT TRAIL</div><h2>Recent wallet events</h2></div><div className="feed-actions">{eventsState !== 'loading' && <button className="text-button" onClick={() => { setEventsState('loading'); setEventsRefresh((value) => value + 1); }}>Refresh</button>}<span className={`status-tag ${eventsState === 'connected' ? 'status-green' : 'status-muted'}`}><i />{eventsState === 'connected' ? 'Live feed' : eventsState === 'loading' ? 'Connecting' : eventsState === 'unavailable' ? 'Not connected' : 'Unavailable'}</span></div></div>
        {events.length ? <div className="event-list">{[...events].reverse().slice(0, 8).map((event) => <div className="event-row" key={`${event.stream_id}-${event.sequence}`}><span className={`event-dot event-${event.type}`} /><span className="event-copy"><strong>{eventLabel(event)}</strong><small>Sequence {event.sequence} · {event.mandate_id ?? 'account'}{event.transaction_id ? ` · ${event.transaction_id}` : ''}</small></span><time>{new Date(event.occurred_at).toLocaleTimeString('en-HK', { hour: 'numeric', minute: '2-digit' })}</time></div>)}</div> : <div className="lab-empty">{eventsState === 'unavailable' ? 'The event feed API is not connected yet.' : eventsState === 'error' ? 'Could not load events. The event feed does not assume an empty ledger.' : 'Loading authorized wallet events…'}</div>}
        {audit?.latest_checkpoint && <div className="checkpoint-row"><FileCheck2 size={15} /><span>Export checkpoint · {audit.latest_checkpoint.stream_id}:{audit.latest_checkpoint.sequence}</span><button className="text-button" onClick={() => void runAuditCheck()} disabled={verifyBusy !== ''}>{verifyBusy === 'audit' ? 'Checking…' : 'Verify history'}</button></div>}
        {auditState === 'connected' && !checkpointAvailable && <div className="checkpoint-row"><Clock3 size={15} /><span>No checkpoint exists for this export.</span><button className="text-button" onClick={() => void createCheckpoint()} disabled={verifyBusy !== ''}>{verifyBusy === 'audit' ? 'Creating…' : 'Create checkpoint'}</button></div>}
        {verifier && <div className={`result-banner ${verifier.valid ? 'result-good' : 'result-warn'}`}><strong>{verifier.status.replace(/_/g, ' ')}</strong><span>{verifier.message}</span><small>Checked through sequence {verifier.checked_through_sequence}; {verifier.unanchored_event_count} later event(s) unanchored.</small></div>}
      </section>
      <section className="panel lab-panel"><div className="panel-heading"><div><div className="eyebrow">SAFETY LAB</div><h2>Can two agents overspend?</h2></div><span className="icon-bubble lavender"><ShieldAlert size={17} /></span></div><p className="lab-description">Compare formal unsafe and atomic models under one HK$400 budget and two HK$300 requests. This runs the solver model; live competing wallet requests are a separate evaluation.</p><button className="button button-secondary lab-run" onClick={() => void runModelComparison()} disabled={verifyBusy !== ''}>{verifyBusy === 'model' ? <span className="spinner spinner-green" /> : <Activity size={15} />}{verifyBusy === 'model' ? 'Running bounded models…' : 'Run unsafe vs atomic models'}</button>
        {models.length > 0 && <div className="model-results">{models.map((result) => <div className="model-result" key={`${result.id}-${result.variant}`}><div><strong>{result.variant === 'unsafe' ? 'Unsafe' : 'Atomic reservation'}</strong><span className={`solver-tag ${result.status === 'inconclusive' ? 'solver-unknown' : result.status === 'counterexample_found' ? 'solver-bad' : 'solver-good'}`}>{result.status.replace(/_/g, ' ')}</span></div><p>{result.message}</p><small>{result.solver_result.toUpperCase()} · bound {result.max_steps} · {result.runtime_ms} ms</small>{result.counterexample.length > 0 && <div className="counterexample">{result.counterexample.map((step) => <div key={`${result.id}-${step.step}`}><b>{step.step}.</b> {step.actor}: {step.action}<small>{step.explanation}</small></div>)}</div>}</div>)}</div>}
        {actionError && <div className="form-error" role="alert">{actionError}</div>}
        <div className="model-limit"><CircleHelp size={14} /><span>Bounded result applies only to this model, bound and listed assumptions. It does not prove the deployed wallet correct.</span></div>
      </section>
    </div>
    <section className="honesty-grid"><div className="honesty-card"><span className="honesty-icon"><Eye size={18} /></span><strong>Prototype catalog</strong><p>{catalogIsPlaceholder ? 'Prices and fees come from a local placeholder. Don’t present them as actual store prices.' : 'Offer sources are returned by the connected catalog.'}</p></div><div className="honesty-card"><span className="honesty-icon"><WalletCards size={18} /></span><strong>Sandbox payment only</strong><p>No card is issued and no real funds move. A completed receipt is a local simulation.</p></div><div className="honesty-card"><span className="honesty-icon"><FileCheck2 size={18} /></span><strong>Evidence has limits</strong><p>An export without an independently retained checkpoint is not a verified audit history.</p></div></section>
    <div className="bottom-note"><span><Shield size={15} /> Live policy decisions come from the wallet service.</span><span>Prototype build · <button>Read safety notes</button></span></div></>;
}

function MandateReviewModal({ initial, busy, onClose, onConfirm }: { initial: Policy; busy: boolean; onClose: () => void; onConfirm: (policy: Policy) => Promise<boolean> }) {
  const [orderCap, setOrderCap] = useState(String(initial.per_order_limit_minor / 100));
  const [weeklyCap, setWeeklyCap] = useState(String(initial.period_limits[0] ? initial.period_limits[0].limit_minor / 100 : 800));
  const [expires, setExpires] = useState(initial.expires_at.slice(0, 10));
  const [blockAlcohol, setBlockAlcohol] = useState(initial.blocked_categories.includes('alcohol'));
  const [error, setError] = useState('');
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const perOrder = Math.round(Number(orderCap) * 100);
    const weekly = Math.round(Number(weeklyCap) * 100);
    if (!Number.isFinite(perOrder) || perOrder < 1 || !Number.isFinite(weekly) || weekly < 1) { setError('Enter valid positive HKD spending limits.'); return; }
    if (!expires || new Date(`${expires}T23:59:59+08:00`) <= new Date()) { setError('Choose an expiry date in the future.'); return; }
    const next: Policy = { ...initial, per_order_limit_minor: perOrder, period_limits: [{ ...initial.period_limits[0], limit_minor: weekly }], expires_at: `${expires}T23:59:59+08:00`, blocked_categories: blockAlcohol ? ['alcohol'] : [] };
    setError('');
    await onConfirm(next);
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}><section className="settings-modal mandate-modal" role="dialog" aria-modal="true" aria-labelledby="mandate-review-title"><div className="modal-heading"><span className="settings-icon"><Shield size={18} /></span><button className="icon-button" aria-label="Close mandate review" onClick={onClose} disabled={busy}><X size={18} /></button></div><div className="eyebrow">REVIEW BEFORE ACTIVATION</div><h2 id="mandate-review-title">Set the spending boundaries</h2><p>These limits will become active only after the wallet confirms them. The assistant cannot raise them.</p><form onSubmit={(event) => void submit(event)}>
    <div className="limit-fields"><label>Max per order <span className="money-input"><i>HK$</i><input aria-label="Maximum per order in HKD" type="number" min="1" step="1" value={orderCap} onChange={(event) => setOrderCap(event.target.value)} /></span></label><label>Weekly limit <span className="money-input"><i>HK$</i><input aria-label="Weekly spending limit in HKD" type="number" min="1" step="1" value={weeklyCap} onChange={(event) => setWeeklyCap(event.target.value)} /></span></label></div>
    <label>Allowed store<input value="Demo Grocery Store A" readOnly /></label><label>Permission expires<input aria-label="Permission expiry date" type="date" value={expires} onChange={(event) => setExpires(event.target.value)} /></label>
    <label className="check-option"><input type="checkbox" checked={blockAlcohol} onChange={(event) => setBlockAlcohol(event.target.checked)} /><span><strong>Block alcohol</strong><small>Items in this category will be refused by the wallet.</small></span></label>
    <div className="settings-note"><ShieldAlert size={16} /><span>Prototype allowance for the local sandbox only. Review the rules and confirm to activate this spending authority.</span></div>
    {error && <div className="form-error" role="alert">{error}</div>}
    <div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose} disabled={busy}>Cancel</button><button type="submit" className="button button-primary" disabled={busy}>{busy ? <span className="spinner" /> : <LockKeyhole size={16} />}{busy ? 'Confirming…' : 'Activate these rules'}</button></div>
  </form></section></div>;
}

function Settings({ token, onToken, mandateId, onMandateId, onClose }: { token: string; onToken: (value: string) => void; mandateId: string; onMandateId: (value: string) => void; onClose: () => void }) {
  const [draftToken, setDraftToken] = useState(token);
  const [draftMandateId, setDraftMandateId] = useState(mandateId);
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title"><div className="modal-heading"><span className="settings-icon"><LockKeyhole size={18} /></span><button className="icon-button" aria-label="Close settings" onClick={onClose}><X size={18} /></button></div><div className="eyebrow">LOCAL DEMO SETTINGS</div><h2 id="settings-title">Connect to your wallet</h2><p>Use the scoped demo <em>user</em> token. Agent credentials never belong in this browser.</p><label>Demo user token<input value={draftToken} onChange={(event) => setDraftToken(event.target.value)} autoComplete="off" spellCheck={false} /></label><label>Mandate ID<input value={draftMandateId} onChange={(event) => setDraftMandateId(event.target.value)} placeholder="Set after confirming the sample mandate" autoComplete="off" /></label><div className="settings-note"><ShieldAlert size={16} /><span>This is prototype authentication. Don’t expose this local dev token on a shared network.</span></div><div className="modal-actions"><button className="button button-secondary" onClick={onClose}>Cancel</button><button className="button button-primary" onClick={() => { onToken(draftToken.trim()); onMandateId(draftMandateId.trim()); onClose(); }}>Save connection</button></div></section></div>;
}

export default App;
