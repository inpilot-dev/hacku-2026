import { useState } from 'react';
import { ArrowRight, Check } from 'lucide-react';
import type { Category, Mandate, PeriodLimit, Policy, StoreConnection } from '../../../../contracts/types';
import { api, ApiError } from '../lib/api';
import { categoryLabel, money, periodWord } from '../lib/format';
import StoresPanel from './StoresPanel';
import './onboarding.css';
import MessageList, { type ConversationMessage } from '../shopping/MessageList';

/*
 * First run (and every new allowance): say who it's for and set the rules, then connect the stores Kumi may shop at.
 * Connecting signs in to the user's own store account so Kumi can fill the real cart; it never checks out.
 * The wallet stays the only authority: these screens only submit a policy for the user to confirm.
 */

const DRAFT_ID = 'draft_demo';
const DELEGATEE_ID = 'agent_student';
const BLOCKABLE: Category[] = ['alcohol', 'beverage_non_alcoholic', 'pantry', 'produce', 'dairy', 'eggs', 'meat', 'seafood', 'bakery', 'household'];
const KNOWN_STORES: Record<string, string> = { wellcome: 'Wellcome', marketplace: 'Market Place' };

export function storeName(id: string) {
  return KNOWN_STORES[id] ?? id;
}

function fourWeeksIso() {
  const end = new Date(Date.now() + 28 * 864e5);
  return `${end.toISOString().slice(0, 10)}T23:59:59+08:00`;
}

type Props = {
  token: string;
  online: boolean | null;
  initialRiskReview?: boolean;
  /** Changing rules: start from the current allowance's policy. */
  initialPolicy?: Policy | null;
  /** Changing rules: keep the name the current allowance is for. */
  initialHolder?: string;
  onActivated: (mandate: Mandate, summary: string, holder: string) => void | Promise<void>;
  onBack?: () => void;
};

const dollars = (minor: number) => String(Math.round(minor / 100));

export default function Onboarding({ token, online, initialRiskReview = false, initialPolicy, initialHolder = '', onActivated, onBack }: Props) {
  const start = initialPolicy ?? null;
  const [step, setStep] = useState<'rules' | 'stores' | 'review'>('rules');
  const [holder, setHolder] = useState(initialHolder);
  const [period, setPeriod] = useState<PeriodLimit['period']>(start?.period_limits[0]?.period ?? 'calendar_week');
  const [weekly, setWeekly] = useState(start?.period_limits[0] ? dollars(start.period_limits[0].limit_minor) : '800');
  const [blocked, setBlocked] = useState<Category[]>(start?.blocked_categories ?? ['alcohol']);
  const [perOrder, setPerOrder] = useState(start ? dollars(start.per_order_limit_minor) : '300');
  const [askOn, setAskOn] = useState(start?.approval_above_minor != null);
  const [askAbove, setAskAbove] = useState(start?.approval_above_minor != null ? dollars(start.approval_above_minor) : '100');
  const [riskReviewOn, setRiskReviewOn] = useState(start?.risk_review ?? initialRiskReview);

  const [stores, setStores] = useState<StoreConnection[] | null>(null);
  const [allowed, setAllowed] = useState<Record<string, boolean>>(() => start
    ? Object.fromEntries(start.allowed_merchant_ids.map((id) => [id, true]))
    : { wellcome: true, marketplace: true });
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [setupMessages, setSetupMessages] = useState<ConversationMessage[]>([]);
  const [reviewPolicy, setReviewPolicy] = useState<Policy | null>(null);

  function rulesValid() {
    const weeklyMinor = Math.round(Number(weekly) * 100);
    const orderMinor = Math.round(Number(perOrder) * 100);
    const askMinor = askOn ? Math.round(Number(askAbove) * 100) : null;
    if (!(weeklyMinor > 0) || !(orderMinor > 0) || (askMinor !== null && !(askMinor > 0))) { setError('Use whole HK$ amounts above zero.'); return null; }
    if (orderMinor > weeklyMinor) { setError(`The most per order can’t be more than the ${periodWord(period)}ly budget.`); return null; }
    return { weeklyMinor, orderMinor, askMinor };
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

  function prepareReview() {
    setError('');
    const rules = rulesValid();
    if (!rules) { setStep('rules'); return; }
    const merchants = Object.entries(allowed).filter(([, on]) => on).map(([id]) => id);
    if (!merchants.length) { setError('Pick at least one store Kumi may shop at.'); return; }
    const policy: Policy = {
      currency: 'HKD',
      per_order_limit_minor: rules.orderMinor,
      period_limits: [{ period, limit_minor: rules.weeklyMinor, timezone: 'Asia/Hong_Kong' }],
      allowed_merchant_ids: merchants,
      blocked_categories: blocked,
      expires_at: fourWeeksIso(),
      approval_above_minor: rules.askMinor,
      risk_review: riskReviewOn,
    };
    setSetupMessages((current) => [...current.filter((m) => m.id !== 'chosen-stores'), { id: 'chosen-stores', speaker: 'you', text: `You may shop at ${merchants.map(storeName).join(', ')}.` }]);
    setReviewPolicy(policy);
    setStep('review');
  }

  async function activate() {
    if (!reviewPolicy || busy) return;
    const policy = reviewPolicy;
    const merchants = policy.allowed_merchant_ids;
    const orderMinor = policy.per_order_limit_minor;
    const periodLimit = policy.period_limits[0];
    setError('');
    setBusy('activate');
    try {
      const names = merchants.map(storeName).join(' and ');
      const per = periodWord(periodLimit.period);
      const never = blocked.length ? `no ${blocked.map((c) => categoryLabel(c).toLowerCase()).join(', ')}` : 'nothing blocked';
      const draftId = await freshDraftId(`Allowance for ${holder.trim() || 'me'}: ${money(orderMinor)} per order and ${money(periodLimit.limit_minor)} per ${per}, only from ${names}, ${never}.`);
      const result = await api.confirm(token, { draft_id: draftId, policy });
      sessionStorage.removeItem('mandate-idempotency-confirm-draft-demo');
      await onActivated(result, `Allowance on: ${money(periodLimit.limit_minor)}/${per}, ${money(orderMinor)}/order at ${names}`, holder.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The wallet could not switch this on.');
    } finally { setBusy(''); }
  }

  const connectedCount = stores?.filter((s) => s.status === 'connected' && allowed[s.store_id]).length ?? 0;

  return <div className="m2-setup-form ob">
    <ol className="ob-steps" aria-label="Setup steps">
      <li className={step === 'rules' ? 'on' : 'done'}><i>{step === 'rules' ? 1 : <Check size={12} />}</i>The rules</li>
      <li className={step === 'stores' ? 'on' : step === 'review' ? 'done' : ''}><i>2</i>Stores</li>
      <li className={step === 'review' ? 'on' : ''}><i>3</i>Confirm</li>
    </ol>
    {error && <p className="ob-error" role="alert">{error}</p>}

    <MessageList messages={[...setupMessages, { id: `setup-${step}`, speaker: step === 'stores' ? 'kumi' : 'kip', text: step === 'rules' ? 'First, tell me who this is for and set the spending limits. These are draft rules until you confirm.' : step === 'stores' ? 'Choose the stores I may use. Connecting your account lets me add items to its cart; it does not place an order.' : 'Here is the exact permission you are granting. Check every rule before switching it on.' }]} />
    {step === 'rules' ? <>
      <h2>The rules</h2>
      <label className="m2-ledger-row"><span>Who it’s for</span><b><input className="ob-holder" value={holder} maxLength={40} placeholder="Me" onChange={(e) => setHolder(e.target.value)} aria-label="Who this allowance is for (leave blank for yourself)" /></b></label>
      <div className="m2-ledger-row"><span>Budget resets</span><div className="ob-seg" role="group" aria-label="Budget period">
        {(['calendar_week', 'calendar_month'] as const).map((p) => <button key={p} type="button" aria-pressed={period === p} className={period === p ? 'on' : ''} onClick={() => setPeriod(p)}>{p === 'calendar_week' ? 'Weekly' : 'Monthly'}</button>)}
      </div></div>
      <label className="m2-ledger-row"><span>{period === 'calendar_month' ? 'Monthly' : 'Weekly'} budget</span><b>HK$<input inputMode="numeric" value={weekly} onChange={(e) => setWeekly(e.target.value)} aria-label={`${period === 'calendar_month' ? 'Monthly' : 'Weekly'} budget in HK$`} /></b></label>
      <label className="m2-ledger-row"><span>Most per order</span><b>HK$<input inputMode="numeric" value={perOrder} onChange={(e) => setPerOrder(e.target.value)} aria-label="Maximum per order in HK$" /></b></label>
      <div className="m2-ledger-row"><span>Ask me first</span><Switch on={askOn} label="Ask me first before bigger orders" onChange={() => setAskOn(!askOn)} /></div>
      {askOn && <label className="m2-ledger-row m2-ledger-sub"><span>For orders above</span><b>HK$<input inputMode="numeric" value={askAbove} onChange={(e) => setAskAbove(e.target.value)} aria-label="Ask me first above this amount in HK$" /></b></label>}
      <div className="m2-ledger-row"><span>Review unusual purchases</span><Switch on={riskReviewOn} label="Review unusual purchases" onChange={() => setRiskReviewOn(!riskReviewOn)} /></div>
      {riskReviewOn && <p className="m2-muted m2-risk-note">Kip can pause first-time or unusually large baskets, new items and sharp price rises. Product listings that try to instruct the agent are always sent for your review.</p>}
      <div className="m2-ledger-row ob-never"><span>Never buy</span><div className="ob-chips" role="group" aria-label="Categories Kip always refuses">
        {BLOCKABLE.map((c) => { const on = blocked.includes(c); return <button key={c} type="button" aria-pressed={on} className={on ? 'on' : ''}
          onClick={() => setBlocked((current) => on ? current.filter((x) => x !== c) : [...current, c])}>{categoryLabel(c)}</button>; })}
      </div></div>
      <div className="m2-ledger-row fixed"><span>Lasts</span><b>4 weeks</b></div>
      <button className="m2-cta" onClick={() => { setError(''); if (rulesValid()) { setSetupMessages([{ id: 'chosen-rules', speaker: 'you', text: `Shop for ${holder.trim() || 'me'}. Budget: HK$${weekly} per ${periodWord(period)}; HK$${perOrder} per order. ${blocked.length ? `Do not buy ${blocked.map(categoryLabel).join(', ')}.` : 'No categories blocked.'}` }]); setStep('stores'); } }}>Next: stores<ArrowRight size={18} /></button>
      {onBack && <button className="m2-link" onClick={onBack}>Back</button>}
    </> : step === 'stores' ? <>
      <h2>Where Kumi may shop</h2>
      <p className="m2-muted ob-small">Sign in to the store accounts the shopping is for, so Kumi can put the basket straight into the real cart. Kumi never checks out: Kip still has to approve every purchase against these rules.</p>
      <StoresPanel token={token} online={online} picked={allowed} onPick={(id, on) => setAllowed((a) => ({ ...a, [id]: on }))} onStores={setStores} onMessage={(text, isError) => setSetupMessages((current) => [...current, { id: crypto.randomUUID(), speaker: 'kumi', text, tone: isError ? 'bad' : 'info' }])} />
      <p className="m2-muted ob-small">{connectedCount ? `Kumi will fill your real cart at ${connectedCount} connected store${connectedCount > 1 ? 's' : ''}.` : 'No store connected yet: Kumi can still price baskets from the store snapshot, but not fill a real cart.'}</p>
      <button className="m2-cta" onClick={prepareReview}>Review permission<ArrowRight size={18} /></button>
      <button className="m2-link" onClick={() => setStep('rules')}>Back to rules</button>
    </> : reviewPolicy && <>
      <h2>Review your permission</h2>
      <dl className="m2-rules">
        <div><dt>Shopping for</dt><dd>{holder.trim() || 'Me'}</dd></div>
        <div><dt>Budget</dt><dd>{money(reviewPolicy.period_limits[0].limit_minor)} per {periodWord(reviewPolicy.period_limits[0].period)}</dd></div>
        <div><dt>Most per order</dt><dd>{money(reviewPolicy.per_order_limit_minor)}, including fees</dd></div>
        <div><dt>May shop at</dt><dd>{reviewPolicy.allowed_merchant_ids.map(storeName).join(', ')}</dd></div>
        <div><dt>Must not buy</dt><dd>{reviewPolicy.blocked_categories.map(categoryLabel).join(', ') || 'No categories blocked'}</dd></div>
        <div><dt>Ask me above</dt><dd>{reviewPolicy.approval_above_minor == null ? 'No extra amount threshold' : money(reviewPolicy.approval_above_minor)}</dd></div>
        <div><dt>Unusual purchase review</dt><dd>{reviewPolicy.risk_review ? 'On' : 'Off'}</dd></div>
        <div><dt>Expires</dt><dd>{new Date(reviewPolicy.expires_at).toLocaleString('en-HK', { timeZone: 'Asia/Hong_Kong' })} HKT</dd></div>
      </dl>
      <p className="m2-muted ob-small">You may pause or revoke this permission. The agent cannot increase these limits. Payments in this prototype are simulated; no real funds move.</p>
      <button className="m2-cta" onClick={() => void activate()} disabled={busy === 'activate' || online === false}>{busy === 'activate' ? 'Activating…' : start ? 'Confirm and replace permission' : 'Confirm and activate permission'}<Check size={18} /></button>
      <button className="m2-link" disabled={Boolean(busy)} onClick={() => setStep('stores')}>Edit stores or rules</button>
    </>}

  </div>;
}

function Switch({ on, label, onChange }: { on: boolean; label: string; onChange: () => void }) {
  return <button type="button" role="switch" aria-checked={on} aria-label={label} className={`m2-switch${on ? ' on' : ''}`} onClick={onChange}><i /></button>;
}
