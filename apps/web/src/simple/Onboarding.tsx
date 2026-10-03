import { useCallback, useEffect, useState } from 'react';
import { ArrowRight, Check } from 'lucide-react';
import type { Mandate, Policy, StoreConnection, StoreConnectionStatus } from '../../../../contracts/types';
import { api, ApiError } from '../lib/api';
import { money } from '../lib/format';
import StoreLoginCanvas from './StoreLoginCanvas';
import './onboarding.css';

/*
 * First run (and every new allowance): set Mum's rules, then connect the stores Kumi may shop at.
 * Connecting signs in to the user's own store account so Kumi can fill the real cart; it never checks out.
 * The wallet stays the only authority: these screens only submit a policy for the user to confirm.
 */

const DRAFT_ID = 'draft_demo';
const DELEGATEE_ID = 'agent_student';
const KNOWN_STORES: Record<string, string> = { wellcome: 'Wellcome' };

export function storeName(id: string) {
  return KNOWN_STORES[id] ?? id;
}

function fourWeeksIso() {
  const end = new Date(Date.now() + 28 * 864e5);
  return `${end.toISOString().slice(0, 10)}T23:59:59+08:00`;
}

const STATUS_TEXT: Record<StoreConnectionStatus, string> = {
  not_connected: 'Not connected',
  awaiting_login: 'Signing in…',
  connected: 'Connected',
  expired: 'Signed out',
};

type Props = {
  token: string;
  online: boolean | null;
  initialRiskReview?: boolean;
  onActivated: (mandate: Mandate, summary: string) => void | Promise<void>;
  onBack?: () => void;
};

export default function Onboarding({ token, online, initialRiskReview = false, onActivated, onBack }: Props) {
  const [step, setStep] = useState<'rules' | 'stores'>('rules');
  const [weekly, setWeekly] = useState('800');
  const [perOrder, setPerOrder] = useState('300');
  const [askOn, setAskOn] = useState(false);
  const [askAbove, setAskAbove] = useState('100');
  const [riskReviewOn, setRiskReviewOn] = useState(initialRiskReview);

  const [stores, setStores] = useState<StoreConnection[] | null>(null);
  const [allowed, setAllowed] = useState<Record<string, boolean>>({ wellcome: true });
  const [signingIn, setSigningIn] = useState<StoreConnection | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const loadStores = useCallback(async () => {
    try {
      setStores((await api.stores(token)).stores);
    } catch (err) {
      // Older APIs have no store connections: fall back to the snapshot shop, sandbox only.
      if (err instanceof ApiError && err.status === 404) setStores([{ store_id: 'wellcome', name: 'Wellcome', status: 'not_connected', connected_at: null, message: 'Real carts are not available on this server.' }]);
      else setError(err instanceof Error ? err.message : 'Could not load your stores.');
    }
  }, [token]);

  useEffect(() => { if (step === 'stores' && !stores) void loadStores(); }, [step, stores, loadStores]);

  function rulesValid() {
    const weeklyMinor = Math.round(Number(weekly) * 100);
    const orderMinor = Math.round(Number(perOrder) * 100);
    const askMinor = askOn ? Math.round(Number(askAbove) * 100) : null;
    if (!(weeklyMinor > 0) || !(orderMinor > 0) || (askMinor !== null && !(askMinor > 0))) { setError('Use whole HK$ amounts above zero.'); return null; }
    return { weeklyMinor, orderMinor, askMinor };
  }

  async function connect(store: StoreConnection) {
    setError(''); setBusy(store.store_id);
    try {
      setSigningIn(await api.connectStore(token, store.store_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not open ${store.name}.`);
    } finally { setBusy(''); }
  }

  async function disconnect(store: StoreConnection) {
    setError(''); setBusy(store.store_id);
    try {
      const next = await api.disconnectStore(token, store.store_id);
      setStores((current) => current?.map((s) => (s.store_id === next.store_id ? next : s)) ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not disconnect ${store.name}.`);
    } finally { setBusy(''); }
  }

  async function finishSignIn(status: StoreConnectionStatus) {
    const store = signingIn;
    setSigningIn(null);
    if (!store) return;
    const next = await api.store(token, store.store_id).catch(() => null);
    if (next) setStores((current) => current?.map((s) => (s.store_id === next.store_id ? next : s)) ?? null);
    if (status !== 'connected') setError(next?.message ?? `${store.name} sign-in didn’t finish.`);
  }

  /** A draft backs exactly one mandate, so every switch-on registers its own; falls back to the seeded draft. */
  async function freshDraftId(text: string): Promise<string> {
    try {
      const draft = await api.draft(token, { text, delegatee_id: DELEGATEE_ID });
      sessionStorage.removeItem('mandate-idempotency-mandate-draft');
      return draft.draft_id;
    } catch (err) {
      if (err instanceof ApiError && [404, 405].includes(err.status)) return DRAFT_ID;
      throw err;
    }
  }

  async function activate() {
    setError('');
    const rules = rulesValid();
    if (!rules) { setStep('rules'); return; }
    const merchants = Object.entries(allowed).filter(([, on]) => on).map(([id]) => id);
    if (!merchants.length) { setError('Pick at least one store Kumi may shop at.'); return; }
    const policy: Policy = {
      currency: 'HKD',
      per_order_limit_minor: rules.orderMinor,
      period_limits: [{ period: 'calendar_week', limit_minor: rules.weeklyMinor, timezone: 'Asia/Hong_Kong' }],
      allowed_merchant_ids: merchants,
      blocked_categories: ['alcohol'],
      expires_at: fourWeeksIso(),
      approval_above_minor: rules.askMinor,
      risk_review: riskReviewOn,
    };
    setBusy('activate');
    try {
      const names = merchants.map(storeName).join(' and ');
      const draftId = await freshDraftId(`Weekly allowance for Mum: ${money(rules.orderMinor)} per order and ${money(rules.weeklyMinor)} per week, only from ${names}, no alcohol.`);
      const result = await api.confirm(token, { draft_id: draftId, policy });
      sessionStorage.removeItem('mandate-idempotency-confirm-draft-demo');
      await onActivated(result, `Allowance on: ${money(rules.weeklyMinor)}/week, ${money(rules.orderMinor)}/order at ${names}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The wallet could not switch this on.');
    } finally { setBusy(''); }
  }

  const connectedCount = stores?.filter((s) => s.status === 'connected' && allowed[s.store_id]).length ?? 0;

  return <div className="m2-setup-form ob">
    <ol className="ob-steps" aria-label="Setup steps">
      <li className={step === 'rules' ? 'on' : 'done'}><i>{step === 'rules' ? 1 : <Check size={12} />}</i>Mum’s rules</li>
      <li className={step === 'stores' ? 'on' : ''}><i>2</i>Her stores</li>
    </ol>
    {error && <p className="ob-error" role="alert">{error}</p>}

    {step === 'rules' ? <>
      <h2>Mum’s rules</h2>
      <label className="m2-ledger-row"><span>Weekly budget</span><b>HK$<input inputMode="numeric" value={weekly} onChange={(e) => setWeekly(e.target.value)} aria-label="Weekly budget in HK$" /></b></label>
      <label className="m2-ledger-row"><span>Most per order</span><b>HK$<input inputMode="numeric" value={perOrder} onChange={(e) => setPerOrder(e.target.value)} aria-label="Maximum per order in HK$" /></b></label>
      <div className="m2-ledger-row"><span>Ask me first</span><Switch on={askOn} label="Ask me first before bigger orders" onChange={() => setAskOn(!askOn)} /></div>
      {askOn && <label className="m2-ledger-row m2-ledger-sub"><span>For orders above</span><b>HK$<input inputMode="numeric" value={askAbove} onChange={(e) => setAskAbove(e.target.value)} aria-label="Ask me first above this amount in HK$" /></b></label>}
      <div className="m2-ledger-row"><span>Review unusual purchases</span><Switch on={riskReviewOn} label="Review unusual purchases" onChange={() => setRiskReviewOn(!riskReviewOn)} /></div>
      {riskReviewOn && <p className="m2-muted m2-risk-note">Kip can pause first-time or unusually large baskets, new items and sharp price rises. Product listings that try to instruct the agent are always sent for your review.</p>}
      <div className="m2-ledger-row fixed"><span>Never buy</span><b>Alcohol</b></div>
      <div className="m2-ledger-row fixed"><span>Lasts</span><b>4 weeks</b></div>
      <button className="m2-cta" onClick={() => { setError(''); if (rulesValid()) setStep('stores'); }}>Next: her stores<ArrowRight size={18} /></button>
      {onBack && <button className="m2-link" onClick={onBack}>Back</button>}
    </> : signingIn ? (
      <StoreLoginCanvas token={token} storeId={signingIn.store_id} storeName={signingIn.name}
        onFinished={(status) => void finishSignIn(status)} onCancel={() => { setSigningIn(null); void loadStores(); }} />
    ) : <>
      <h2>Where Kumi may shop</h2>
      <p className="m2-muted ob-small">Sign in to Mum’s own store accounts so Kumi can put the basket straight into the real cart. Kumi never checks out: Kip still has to approve every purchase against these rules.</p>
      {!stores ? <p className="m2-muted">Loading stores…</p> : <ul className="ob-stores">
        {stores.map((store) => <li key={store.store_id}>
          <label className="ob-store-pick">
            <input type="checkbox" checked={Boolean(allowed[store.store_id])} onChange={(e) => setAllowed((a) => ({ ...a, [store.store_id]: e.target.checked }))} />
            <span><b>{store.name}</b><small className={`ob-status ${store.status}`}>{STATUS_TEXT[store.status]}</small></span>
          </label>
          {store.status === 'connected'
            ? <button className="m2-link" onClick={() => void disconnect(store)} disabled={busy === store.store_id}>Disconnect</button>
            : <button className="ob-connect" onClick={() => void connect(store)} disabled={busy === store.store_id || online === false}>{store.status === 'expired' ? 'Sign in again' : 'Connect'}</button>}
        </li>)}
      </ul>}
      <p className="m2-muted ob-small">{connectedCount ? `Kumi will fill your real cart at ${connectedCount} connected store${connectedCount > 1 ? 's' : ''}.` : 'No store connected yet: Kumi can still price baskets from the store snapshot, but not fill a real cart.'}</p>
      <button className="m2-cta" onClick={() => void activate()} disabled={busy === 'activate' || online === false}>{busy === 'activate' ? 'Switching on…' : 'Switch on Mum’s card'}<ArrowRight size={18} /></button>
      <button className="m2-link" onClick={() => setStep('rules')}>Back to rules</button>
    </>}
  </div>;
}

function Switch({ on, label, onChange }: { on: boolean; label: string; onChange: () => void }) {
  return <button type="button" role="switch" aria-checked={on} aria-label={label} className={`m2-switch${on ? ' on' : ''}`} onClick={onChange}><i /></button>;
}
