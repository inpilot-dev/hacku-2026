import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { LucideIcon } from 'lucide-react';
import { ArrowRight, Carrot, Cherry, Snowflake, Wine } from 'lucide-react';
import './openai-tokens.css';
import type { ApprovalRequest, AuditEvent, BudgetResponse, CatalogResponse, Mandate, Policy, Product, Quote, Receipt, RuleViolation } from '../../../../contracts/types';
import { api, ApiError } from '../lib/api';
import { money } from '../lib/format';

/*
 * A single-screen take on the core Mandate story for a Gen Z caregiver:
 * set Mum's allowance, let Kumi fill a basket, watch Kip (the wallet) pay or refuse, freeze it any time.
 * The wallet API stays the only authority; this screen never decides anything itself.
 */

const TOKEN = 'dev-user-token';
const STORE_ID = 'wellcome';
const PICKUP_CONTEXT_ID = 'ctx_wellcome_click_collect';
const DRAFT_ID = 'draft_demo';
const MANDATE_KEY = 'mandate-id';

type Mascot = 'kumi' | 'kip' | 'bean' | 'stella';
type Line = { product_id: string; quantity: number };
type Pick = { id: string; icon: LucideIcon; title: string; subtitle: string; tone: 'green' | 'orange' | 'pink'; items: Line[]; list: string[] };
type Verdict =
  | { kind: 'paid'; receipt: Receipt; replayed: boolean }
  | { kind: 'refused'; message: string; violations: RuleViolation[] }
  | { kind: 'review'; message: string; violations: RuleViolation[]; approval: ApprovalRequest }
  | { kind: 'uncertain'; message: string };
type Phase = 'pick' | 'packing' | 'basket' | 'paying' | 'verdict';
type LogEntry = { id: string; at: string; who: Mascot; text: string; tone: 'good' | 'bad' | 'info'; state?: string };

const PICKS: Pick[] = [
  {
    id: 'basics', icon: Carrot, title: 'Weekly basics', subtitle: 'Rice, milk, greens, apples, tea', tone: 'green',
    items: [
      { product_id: 'wellcome_101322993', quantity: 1 },
      { product_id: 'wellcome_101355093', quantity: 1 },
      { product_id: 'wellcome_101378087', quantity: 1 },
      { product_id: 'wellcome_102127176', quantity: 3 },
      { product_id: 'wellcome_101343395', quantity: 1 },
    ],
    list: ['jasmine rice 5kg', 'fresh milk 1L', 'broccoli', '3 apples', 'no-sugar jasmine tea'],
  },
  {
    id: 'fruit', icon: Cherry, title: 'Fruit & tea run', subtitle: 'Grapes, blueberries, kiwis, oolong', tone: 'orange',
    items: [
      { product_id: 'wellcome_101869136', quantity: 1 },
      { product_id: 'wellcome_101374428', quantity: 1 },
      { product_id: 'wellcome_101373345', quantity: 2 },
      { product_id: 'wellcome_101343041', quantity: 1 },
    ],
    list: ['shine muscat grapes', 'blueberries', '2 kiwis', 'no-sugar oolong tea'],
  },
  {
    id: 'champagne', icon: Wine, title: 'Sneak in champagne', subtitle: 'Test the rules: should be refused', tone: 'pink',
    items: [
      { product_id: 'wellcome_113277654', quantity: 1 },
      { product_id: 'wellcome_101355093', quantity: 1 },
    ],
    list: ['champagne case', 'fresh milk 1L'],
  },
];

const REASONS: Partial<Record<RuleViolation['code'], string>> = {
  ORDER_CAP_EXCEEDED: 'Over the per-order limit',
  PERIOD_BUDGET_EXCEEDED: 'Would blow this week’s budget',
  MERCHANT_NOT_ALLOWED: 'That shop isn’t on the list',
  CATEGORY_BLOCKED: 'Something in the basket is off-limits',
  CATEGORY_REVIEW_REQUIRED: 'This one needs your OK',
  MANDATE_NOT_ACTIVE: 'The card isn’t active',
  MANDATE_EXPIRED: 'The allowance has expired',
  MANDATE_REVOKED: 'The card is frozen',
};

function nextSundayIso() {
  const end = new Date(Date.now() + 28 * 864e5);
  return `${end.toISOString().slice(0, 10)}T23:59:59+08:00`;
}

function Avatar({ name, state, size = 64, bob = false }: { name: Mascot; state: string; size?: number; bob?: boolean }) {
  return <img className={`m2-avatar${bob ? ' m2-bob' : ''}`} src={`/agents/${name}-${state}.png`} alt="" width={size} height={size} />;
}

function Sticker({ name, state, size = 88, tilt = -6 }: { name: Mascot; state: string; size?: number; tilt?: number }) {
  return <span className={`m2-sticker ${name}`} style={{ width: size, height: size, transform: `rotate(${tilt}deg)` }}><img src={`/agents/${name}-${state}.png`} alt="" /></span>;
}

const hkd = (minor: number) => money(minor).replace('HK$', 'HK$\u202F');

function eventText(event: AuditEvent): Omit<LogEntry, 'id' | 'at'> | null {
  const amount = typeof event.payload.amount_minor === 'number' ? ` ${money(event.payload.amount_minor)}` : '';
  switch (event.type) {
    case 'mandate_confirmed': return { who: 'bean', text: 'Allowance switched on', tone: 'good' };
    case 'mandate_revoked': return { who: 'kip', text: 'Card frozen', tone: 'bad', state: 'revoked' };
    case 'quote_created': return { who: 'kumi', text: 'Kumi priced a basket', tone: 'info' };
    case 'authorization_refused': return { who: 'kip', text: 'Kip refused a purchase', tone: 'bad' };
    case 'payment_completed': return { who: 'kip', text: `Kip paid${amount}`, tone: 'good' };
    case 'payment_refused': return { who: 'kip', text: 'Payment refused', tone: 'bad' };
    default: return null;
  }
}

export default function SimpleApp() {
  const [online, setOnline] = useState<boolean | null>(null);
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [mandateId, setMandateId] = useState(() => localStorage.getItem(MANDATE_KEY) ?? '');
  const [mandate, setMandate] = useState<Mandate | null>(null);
  const [budget, setBudget] = useState<BudgetResponse | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const [weekly, setWeekly] = useState('800');
  const [perOrder, setPerOrder] = useState('300');
  const [askAbove, setAskAbove] = useState('');
  const [setupOpen, setSetupOpen] = useState(false);

  const [phase, setPhase] = useState<Phase>('pick');
  const [pick, setPick] = useState<Pick | null>(null);
  const [custom, setCustom] = useState<Record<string, number>>({});
  const [showCustom, setShowCustom] = useState(false);
  const [search, setSearch] = useState('');
  const [quote, setQuote] = useState<Quote | null>(null);
  const [routeId, setRouteId] = useState<string | null>(null);
  const [routeLabel, setRouteLabel] = useState('');
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [agentNote, setAgentNote] = useState('');

  const [log, setLog] = useState<LogEntry[]>([]);
  const [serverLog, setServerLog] = useState<LogEntry[] | null>(null);
  const [showFine, setShowFine] = useState(false);
  const shopRef = useRef<HTMLElement>(null);

  const note = useCallback((who: Mascot, text: string, tone: LogEntry['tone'], state?: string) => {
    setLog((current) => [{ id: crypto.randomUUID(), at: new Date().toISOString(), who, text, tone, state }, ...current].slice(0, 20));
  }, []);

  const refresh = useCallback(async (id = mandateId) => {
    try { await api.health(); setOnline(true); } catch { setOnline(false); }
    if (!id) { setMandate(null); setBudget(null); setLoaded(true); return; }
    try {
      const [m, b] = await Promise.all([api.mandate(TOKEN, id), api.budget(TOKEN, id)]);
      setMandate(m); setBudget(b);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) { localStorage.removeItem(MANDATE_KEY); setMandateId(''); setMandate(null); setBudget(null); }
    } finally { setLoaded(true); }
    try {
      const result = await api.events(TOKEN, 0, 100);
      setServerLog(result.events.filter((event) => !event.mandate_id || event.mandate_id === id).reverse().slice(0, 12).flatMap((event) => {
        const text = eventText(event);
        return text ? [{ ...text, id: event.event_id, at: event.occurred_at }] : [];
      }));
    } catch { setServerLog(null); }
  }, [mandateId]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { api.catalog(TOKEN).then(setCatalog).catch(() => setCatalog(null)); }, []);

  const products = useMemo(() => catalog?.products.filter((p) => p.merchant_id === STORE_ID && p.available) ?? [], [catalog]);
  const productById = useMemo(() => new Map(products.map((p) => [p.id, p])), [products]);
  const snapshotAt = useMemo(() => {
    const times = catalog?.evidence.filter((e) => e.kind === 'product_price').map((e) => e.observed_at).sort() ?? [];
    return times.length ? new Date(times[times.length - 1]).toLocaleString('en-HK', { dateStyle: 'medium', timeStyle: 'short' }) : null;
  }, [catalog]);

  const active = mandate?.status === 'active';
  const period = budget?.applicable_budgets[0];
  const available = period?.available_minor ?? mandate?.policy.period_limits[0]?.limit_minor ?? 0;
  const limit = period?.limit_minor ?? mandate?.policy.period_limits[0]?.limit_minor ?? 0;
  const spent = (period?.paid_minor ?? 0) + (period?.reserved_minor ?? 0);
  const ratio = limit ? Math.min(1, spent / limit) : 0;

  async function activate() {
    setError('');
    const weeklyMinor = Math.round(Number(weekly) * 100);
    const orderMinor = Math.round(Number(perOrder) * 100);
    const askMinor = askAbove.trim() ? Math.round(Number(askAbove) * 100) : null;
    if (!(weeklyMinor > 0) || !(orderMinor > 0) || (askMinor !== null && !(askMinor > 0))) { setError('Use whole HK$ amounts above zero.'); return; }
    const policy: Policy = {
      currency: 'HKD',
      per_order_limit_minor: orderMinor,
      period_limits: [{ period: 'calendar_week', limit_minor: weeklyMinor, timezone: 'Asia/Hong_Kong' }],
      allowed_merchant_ids: [STORE_ID],
      blocked_categories: ['alcohol'],
      expires_at: nextSundayIso(),
      approval_above_minor: askMinor,
    };
    setBusy('activate');
    try {
      const result = await api.confirm(TOKEN, { draft_id: DRAFT_ID, policy });
      sessionStorage.removeItem('mandate-idempotency-confirm-draft-demo');
      localStorage.setItem(MANDATE_KEY, result.id);
      setMandateId(result.id); setMandate(result); setSetupOpen(false); resetShop();
      note('bean', `Allowance on: ${money(weeklyMinor)}/week, ${money(orderMinor)}/order`, 'good');
      await refresh(result.id);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409
        ? 'This demo wallet only has one setup slot and it’s used. Restart the demo for a fresh wallet.'
        : err instanceof Error ? err.message : 'The wallet could not switch this on.');
    } finally { setBusy(''); }
  }

  async function freeze() {
    if (!mandate) return;
    setBusy('freeze'); setError('');
    try {
      const result = await api.revoke(TOKEN, mandate.id);
      sessionStorage.removeItem(`mandate-idempotency-revoke-${mandate.id}`);
      setMandate(result.mandate); resetShop();
      note('kip', `Card frozen${result.cancelled_reservation_ids.length ? `, ${result.cancelled_reservation_ids.length} hold(s) released` : ''}`, 'bad', 'revoked');
      await refresh(mandate.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not freeze the card.');
    } finally { setBusy(''); }
  }

  function resetShop() {
    setPhase('pick'); setPick(null); setQuote(null); setVerdict(null); setAgentNote(''); setRouteId(null); setRouteLabel('');
  }

  /** Ask the agent service first; if it isn't mounted, use the preset basket and say so. */
  async function packWithAgent(chosen: Pick): Promise<Quote | null> {
    if (!mandate) return null;
    try {
      const run = await api.startAgentRun(TOKEN, { mandate_id: mandate.id, shopping_list: chosen.list.map((name) => ({ name, quantity: 1 })), auto_purchase: false });
      let current = run;
      for (let tries = 0; tries < 20 && !current.quote_id && !['failed', 'refused', 'completed'].includes(current.status); tries += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        current = await api.agentRun(TOKEN, run.id);
      }
      if (current.quote_id) { setAgentNote(`Packed by ${current.provider === 'jev' ? 'the shopping agent' : current.provider}`); return await api.quoteById(TOKEN, current.quote_id); }
      return null;
    } catch {
      return null;
    }
  }

  async function shop(chosen: Pick | 'custom') {
    if (!mandate || !active) return;
    const lines = chosen === 'custom'
      ? Object.entries(custom).filter(([, q]) => q > 0).map(([product_id, quantity]) => ({ product_id, quantity }))
      : chosen.items.filter((line) => productById.has(line.product_id));
    if (!lines.length) { setError('That basket is empty in the current catalog.'); return; }
    setError(''); setVerdict(null); setPick(chosen === 'custom' ? null : chosen); setPhase('packing');
    shopRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    try {
      let result = chosen === 'custom' ? null : await packWithAgent(chosen);
      if (!result) {
        if (chosen !== 'custom') setAgentNote('Preset basket · agent not connected here');
        else setAgentNote('Picked by you');
        result = await api.quote(TOKEN, { merchant_id: STORE_ID, delivery_context_id: PICKUP_CONTEXT_ID, items: lines });
      }
      setQuote(result);
      note('kumi', `Kumi packed ${result.items.reduce((n, item) => n + item.quantity, 0)} items · ${money(result.total_minor)}`, 'info');
      try {
        const options = await api.paymentOptions(TOKEN, result.id);
        const chosenRoute = options.options.find((o) => o.route_id === options.recommended_route_id) ?? options.options.find((o) => o.eligible);
        setRouteId(chosenRoute?.route_id ?? null); setRouteLabel(chosenRoute?.label ?? '');
      } catch { setRouteId(null); setRouteLabel(''); }
      setPhase('basket');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kumi could not price that basket.');
      setPhase('pick');
    }
  }

  async function checkout(approvalId?: string) {
    if (!mandate || !quote) return;
    setPhase('paying'); setError('');
    const key = `mandate-tx-${quote.id}`;
    const transactionId = sessionStorage.getItem(key) ?? crypto.randomUUID();
    sessionStorage.setItem(key, transactionId);
    await new Promise((resolve) => window.setTimeout(resolve, 900)); // let Kip's check read as a moment, not a flicker
    try {
      const result = await api.demoPurchase(TOKEN, { mandate_id: mandate.id, quote_id: quote.id, transaction_id: transactionId, payment_route_id: routeId, approval_id: approvalId ?? null });
      const auth = result.authorization;
      if (auth.status === 'requires_review' && auth.approval_request) {
        setVerdict({ kind: 'review', message: auth.message, violations: auth.violations, approval: auth.approval_request });
        note('kip', `Kip wants your OK for ${money(quote.total_minor)}`, 'info');
      } else if (auth.status !== 'approved') {
        setVerdict({ kind: 'refused', message: auth.message, violations: auth.violations });
        note('kip', `Kip blocked ${money(quote.total_minor)}`, 'bad');
      } else if (result.payment?.status === 'completed') {
        setVerdict({ kind: 'paid', receipt: result.payment.receipt, replayed: result.payment.replayed });
        note('kip', `Kip paid ${money(result.payment.receipt.amount_minor)}`, 'good');
      } else if (result.payment?.status === 'refused') {
        setVerdict({ kind: 'refused', message: result.payment.message, violations: result.payment.violations });
        note('kip', 'Payment refused', 'bad');
      } else {
        setVerdict({ kind: 'uncertain', message: 'The wallet approved it but hasn’t confirmed payment yet.' });
      }
    } catch (err) {
      if (err instanceof ApiError && [401, 403, 404, 422, 503].includes(err.status)) {
        setVerdict({ kind: 'refused', message: `${err.message} No payment was made.`, violations: [] });
      } else {
        try {
          const receipt = await api.paymentByTransaction(TOKEN, transactionId);
          setVerdict({ kind: 'paid', receipt, replayed: true });
        } catch {
          setVerdict({ kind: 'uncertain', message: 'We lost the connection mid-checkout. Checking again reuses the same transaction, so it can’t pay twice.' });
        }
      }
    }
    setPhase('verdict');
    shopRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    void refresh(mandate.id);
  }

  async function decide(approve: boolean) {
    if (verdict?.kind !== 'review') return;
    setBusy('decide');
    try {
      if (approve) {
        await api.approve(TOKEN, verdict.approval.id);
        note('bean', 'You approved it once', 'good');
        await checkout(verdict.approval.id);
      } else {
        await api.deny(TOKEN, verdict.approval.id);
        note('bean', 'You said no', 'info');
        setVerdict({ kind: 'refused', message: 'You declined this one. Nothing was paid.', violations: [] });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not record your decision.');
    } finally { setBusy(''); }
  }

  const customCount = Object.values(custom).reduce((sum, q) => sum + q, 0);
  const customTotal = Object.entries(custom).reduce((sum, [id, q]) => sum + (productById.get(id)?.unit_price_minor ?? 0) * q, 0);
  const filtered = products.filter((p) => p.title.toLowerCase().includes(search.trim().toLowerCase())).slice(0, 24);
  const feed = serverLog && serverLog.length ? serverLog : log;
  const showSetup = loaded && (!mandate || setupOpen);

  const kipState = !mandate || !active ? 'revoked' : phase === 'verdict' && verdict?.kind === 'paid' ? 'approved' : phase === 'verdict' && verdict?.kind === 'refused' ? 'refused' : 'idle';

  const estimate = (p: Pick) => p.items.reduce((sum, line) => sum + (productById.get(line.product_id)?.unit_price_minor ?? 0) * line.quantity, 0);
  const expires = mandate ? new Date(mandate.policy.expires_at).toLocaleDateString('en-HK', { day: 'numeric', month: 'short' }) : '';
  const stamp = verdict?.kind === 'paid' ? 'Paid' : verdict?.kind === 'refused' ? 'Refused' : verdict?.kind === 'review' ? 'Your call' : 'Checking';

  return <div className="m2">
    <header className="m2-top">
      <div className="m2-brand">Mandate</div>
      <span className={`m2-pill ${online === false ? 'off' : ''}`}><i />{online === false ? 'Wallet offline' : 'Sandbox, no real money'}</span>
    </header>

    {error && <div className="m2-error" role="alert"><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss">Dismiss</button></div>}

    {!loaded ? <div className="m2-loading"><Avatar name="kip" state="idle" size={96} bob /></div> : showSetup ? (
      <section className="m2-setup">
        <div className="m2-setup-copy">
          <p className="m2-kicker">For the person who does Mum’s shopping</p>
          <h1>Mum’s groceries, <em>on a card that can’t go rogue.</em></h1>
          <p className="m2-lede">Kumi shops for her. Kip, the wallet, only pays when your rules say yes. You can freeze it in one tap.</p>
          <div className="m2-cast">
            <figure><Sticker name="kumi" state="idle" size={76} tilt={-5} /><figcaption><b>Kumi</b> fills the basket</figcaption></figure>
            <figure><Sticker name="kip" state="idle" size={76} tilt={4} /><figcaption><b>Kip</b> holds the money</figcaption></figure>
            <figure><Sticker name="stella" state="idle" size={76} tilt={-3} /><figcaption><b>Stella</b> keeps the log</figcaption></figure>
          </div>
        </div>
        <div className="m2-setup-form">
          <h2>Mum’s rules</h2>
          <label className="m2-ledger-row"><span>Weekly budget</span><b>HK$<input inputMode="numeric" value={weekly} onChange={(e) => setWeekly(e.target.value)} aria-label="Weekly budget in HK$" /></b></label>
          <label className="m2-ledger-row"><span>Most per order</span><b>HK$<input inputMode="numeric" value={perOrder} onChange={(e) => setPerOrder(e.target.value)} aria-label="Maximum per order in HK$" /></b></label>
          <label className="m2-ledger-row"><span>Ask me first above <small>optional</small></span><b>HK$<input inputMode="numeric" placeholder="off" value={askAbove} onChange={(e) => setAskAbove(e.target.value)} aria-label="Ask me first above this amount in HK$" /></b></label>
          <div className="m2-ledger-row fixed"><span>Shop</span><b>Wellcome</b></div>
          <div className="m2-ledger-row fixed"><span>Never buy</span><b>Alcohol</b></div>
          <div className="m2-ledger-row fixed"><span>Lasts</span><b>4 weeks</b></div>
          <button className="m2-cta" onClick={() => void activate()} disabled={busy === 'activate' || online === false}>{busy === 'activate' ? 'Switching on…' : 'Switch on Mum’s card'}<ArrowRight size={18} /></button>
          {setupOpen && mandate && <button className="m2-link" onClick={() => setSetupOpen(false)}>Back</button>}
        </div>
      </section>
    ) : mandate && (
      <main className="m2-grid">
        <section className="m2-col m2-area-card">
          <div className={`m2-card ${active ? '' : 'frozen'}`}>
            <div className="m2-card-top"><span>Mum’s grocery card</span><span className="m2-status">{active ? 'Active' : mandate.status === 'expired' ? 'Expired' : 'Frozen'}</span></div>
            <div className="m2-card-amount"><small>{active ? 'Left this week' : 'Spending is off'}</small><b>{active ? hkd(available) : 'HK$ 0'}</b></div>
            <div className="m2-meter" aria-hidden>{Array.from({ length: 20 }, (_, i) => <i key={i} className={i < Math.round(ratio * 20) ? 'on' : ''} />)}</div>
            <div className="m2-card-foot"><span>{hkd(spent)} used</span><span>{hkd(limit)} a week</span></div>
            <div className="m2-card-sticker"><Sticker name="kip" state={kipState} size={104} tilt={8} /></div>
          </div>
          <dl className="m2-rules">
            <div><dt>Most per order</dt><dd>{hkd(mandate.policy.per_order_limit_minor)}</dd></div>
            <div><dt>Shop</dt><dd>Wellcome</dd></div>
            <div><dt>Never buy</dt><dd>Alcohol</dd></div>
            {mandate.policy.approval_above_minor != null && <div><dt>Ask me first above</dt><dd>{hkd(mandate.policy.approval_above_minor)}</dd></div>}
            <div><dt>Ends</dt><dd>{expires}</dd></div>
          </dl>
          {active
            ? <button className="m2-freeze" onClick={() => void freeze()} disabled={busy === 'freeze'}><Snowflake size={18} />{busy === 'freeze' ? 'Freezing…' : 'Freeze the card'}</button>
            : <button className="m2-cta" onClick={() => setSetupOpen(true)}>Start a new allowance<ArrowRight size={18} /></button>}
        </section>

        <section className="m2-col m2-area-feed">
          <div className="m2-feed">
            <div className="m2-feed-head"><Sticker name="stella" state={feed.length ? 'pass' : 'idle'} size={46} tilt={-8} /><div><strong>Stella’s log</strong><small>{serverLog && serverLog.length ? 'From the wallet’s audit trail' : 'This session'}</small></div></div>
            {feed.length ? <ol>{feed.map((entry) => <li key={entry.id} className={entry.tone}><time>{new Date(entry.at).toLocaleTimeString('en-HK', { hour: '2-digit', minute: '2-digit', hour12: false })}</time><span className={`m2-dot ${entry.who}`} /><span>{entry.text}</span></li>)}</ol>
              : <p className="m2-empty">Nothing yet. Send Kumi shopping.</p>}
          </div>
        </section>

        <section className="m2-col m2-area-shop" ref={shopRef}>
          {!active ? (
            <div className="m2-panel m2-center">
              <Sticker name="kip" state="revoked" size={150} tilt={-4} />
              <h2>Kip is asleep.</h2>
              <p className="m2-muted">The card is {mandate.status === 'expired' ? 'expired' : 'frozen'}. Nobody can spend from it, not even Kumi.</p>
            </div>
          ) : phase === 'pick' ? (
            <div className="m2-panel">
              <div className="m2-panel-head"><div><p className="m2-kicker">Kumi’s turn</p><h2>What does Mum need this week?</h2></div><Sticker name="kumi" state="idle" size={84} tilt={6} /></div>
              <div className="m2-picks">
                {PICKS.map((p) => { const Icon = p.icon; return <button key={p.id} className={`m2-pick ${p.tone}`} onClick={() => void shop(p)} disabled={!products.length}>
                  <span className="m2-tile"><Icon size={24} strokeWidth={2.2} /></span>
                  <span className="m2-pick-text"><b>{p.title}</b><small>{p.subtitle}</small></span>
                  <span className="m2-pick-price">{products.length ? `~${hkd(estimate(p))}` : ''}</span>
                </button>; })}
              </div>
              <button className="m2-link" onClick={() => setShowCustom((v) => !v)}>{showCustom ? 'Hide the shelf' : 'Or pick items yourself'}</button>
              {showCustom && <div className="m2-shelf">
                <input className="m2-search" placeholder="Search Wellcome" value={search} onChange={(e) => setSearch(e.target.value)} />
                <ul>{filtered.map((p: Product) => <li key={p.id}>
                  <span className="m2-shelf-title">{p.title}{p.category === 'alcohol' && <em> alcohol</em>}</span>
                  <b>{hkd(p.unit_price_minor)}</b>
                  <span className="m2-qty">
                    {(custom[p.id] ?? 0) > 0 && <><button onClick={() => setCustom((c) => ({ ...c, [p.id]: Math.max(0, (c[p.id] ?? 0) - 1) }))} aria-label={`Remove one ${p.title}`}>−</button><i>{custom[p.id]}</i></>}
                    <button onClick={() => setCustom((c) => ({ ...c, [p.id]: Math.min(20, (c[p.id] ?? 0) + 1) }))} aria-label={`Add one ${p.title}`}>+</button>
                  </span>
                </li>)}</ul>
                <button className="m2-cta" disabled={!customCount} onClick={() => void shop('custom')}>{customCount ? `Price ${customCount} item${customCount > 1 ? 's' : ''}, about ${hkd(customTotal)}` : 'Add something first'}</button>
              </div>}
            </div>
          ) : phase === 'packing' ? (
            <div className="m2-panel m2-center">
              <Sticker name="kumi" state="idle" size={150} tilt={-5} />
              <h2>Kumi is packing{pick ? ` ${pick.title.toLowerCase()}` : ''}…</h2>
              <p className="m2-muted">Pricing it against Wellcome’s shelf.</p>
              <div className="m2-dots"><i /><i /><i /></div>
            </div>
          ) : quote && (phase === 'basket' || phase === 'paying' || phase === 'verdict') ? (
            <div className="m2-panel m2-checkout">
              <div className="m2-panel-head">
                <div><p className="m2-kicker">{phase === 'verdict' ? 'Kip’s decision' : agentNote}</p><h2>{phase === 'verdict' && verdict ? verdictTitle(verdict, quote.total_minor) : phase === 'paying' ? 'Kip is checking Mum’s rules…' : 'Basket’s ready.'}</h2></div>
                <Sticker name={phase === 'verdict' && verdict?.kind === 'review' ? 'bean' : phase === 'verdict' ? 'kip' : 'kumi'} state={phase === 'verdict' && verdict ? (verdict.kind === 'paid' ? 'approved' : verdict.kind === 'refused' ? 'refused' : 'idle') : phase === 'paying' ? 'idle' : 'happy'} size={phase === 'verdict' ? 112 : 84} tilt={phase === 'verdict' ? -7 : 6} />
              </div>
              <div className="m2-receipt">
                <div className="m2-receipt-head"><span>WELLCOME · CLICK &amp; COLLECT</span><span>{new Date(quote.created_at).toLocaleDateString('en-HK', { day: '2-digit', month: 'short' })}</span></div>
                <ul>{quote.items.map((item) => <li key={item.product_id} className={productById.get(item.product_id)?.category === 'alcohol' ? 'flag' : ''}><span>{item.quantity}×</span><span>{item.title}</span><b>{money(item.line_total_minor)}</b></li>)}
                  {quote.charges.map((charge, i) => <li key={`c${i}`} className="m2-charge"><span /><span>{charge.label}</span><b>{charge.amount_minor ? money(charge.amount_minor) : 'FREE'}</b></li>)}
                </ul>
                <div className="m2-receipt-total"><span>TOTAL</span><b>{money(quote.total_minor)}</b></div>
                {phase === 'verdict' && verdict?.kind === 'paid' && <div className="m2-receipt-meta"><span>Receipt</span><span>{verdict.receipt.id.slice(0, 18)}…</span>{verdict.receipt.payment_route && <><span>Paid via</span><span>{verdict.receipt.payment_route.label}</span></>}<span>Left this week</span><span>{money(available)}</span></div>}
                {phase === 'verdict' && verdict && <div className={`m2-stamp ${verdict.kind}`}>{stamp}</div>}
              </div>
              {phase === 'basket' && <>
                <button className="m2-cta" onClick={() => void checkout()}>Let Kip pay {hkd(quote.total_minor)}<ArrowRight size={18} /></button>
                {routeLabel && <p className="m2-route">Kip will use <b>{routeLabel}</b>, the cheapest route it found.</p>}
                <button className="m2-link" onClick={resetShop}>Start over</button>
              </>}
              {phase === 'paying' && <div className="m2-dots center"><i /><i /><i /></div>}
              {phase === 'verdict' && verdict?.kind === 'refused' && <>
                {verdict.violations.length > 0 ? <ul className="m2-why">{verdict.violations.map((v, i) => <li key={`${v.rule_id}-${i}`}><b>{REASONS[v.code] ?? v.code.replace(/_/g, ' ').toLowerCase()}</b><small>{v.message}</small></li>)}</ul> : <p className="m2-muted">{verdict.message}</p>}
                <p className="m2-muted">Nothing was paid.</p>
              </>}
              {phase === 'verdict' && verdict?.kind === 'paid' && <p className="m2-muted">Within every rule. It’s ready to collect at Wellcome.</p>}
              {phase === 'verdict' && verdict?.kind === 'uncertain' && <><p className="m2-muted">{verdict.message}</p><button className="m2-cta" onClick={() => void checkout()}>Check again</button></>}
              {phase === 'verdict' && verdict?.kind === 'review' && <>
                <p className="m2-muted">It’s over your “ask me first” line of {mandate.policy.approval_above_minor != null ? hkd(mandate.policy.approval_above_minor) : 'the limit'}. Approving lets this one order through once.</p>
                <div className="m2-row">
                  <button className="m2-cta" onClick={() => void decide(true)} disabled={busy === 'decide'}>Approve once</button>
                  <button className="m2-ghost" onClick={() => void decide(false)} disabled={busy === 'decide'}>Say no</button>
                </div>
              </>}
              {phase === 'verdict' && verdict?.kind !== 'review' && <button className="m2-ghost" onClick={resetShop}>Shop again</button>}
            </div>
          ) : null}
        </section>
      </main>
    )}

    <footer className="m2-foot">
      <button className="m2-link" onClick={() => setShowFine((v) => !v)}>How this works</button>
      {showFine && <div className="m2-fine-print">
        <p><b>Kip has the final say.</b> Kumi, the shopping agent, can only suggest a basket. Kip is a separate wallet service. It checks every purchase against your rules, then signs it or refuses it.</p>
        <p><b>Prices</b> come from a Wellcome snapshot{snapshotAt ? ` taken ${snapshotAt}` : ''}. Click &amp; Collect only, free above HK$50. Offers may have changed.</p>
        <p><b>Payments</b> run in a sandbox. No real money moves.</p>
        <a href="?classic">Open the full dashboard (evidence, audit, safety lab)</a>
      </div>}
    </footer>
  </div>;
}

function verdictTitle(verdict: Verdict, total: number) {
  if (verdict.kind === 'paid') return `Paid. ${hkd(verdict.receipt.amount_minor)}.`;
  if (verdict.kind === 'refused') return 'Nope. Kip said no.';
  if (verdict.kind === 'review') return `Kumi wants to spend ${hkd(total)}.`;
  return 'Still checking.';
}
