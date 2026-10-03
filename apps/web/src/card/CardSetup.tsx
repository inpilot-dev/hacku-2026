import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { ArrowLeft, ArrowRight, Check } from 'lucide-react';
import '../simple/openai-tokens.css';
import type { CardAuthorization, Mandate, VirtualCard } from '../../../../contracts/types';
import { api, ApiError } from '../lib/api';
import { money } from '../lib/format';

/*
 * Virtual card setup: the wallet issues the card when an allowance is confirmed, so this page walks the owner
 * through what was issued, the controls the issuer enforces, and the lock. The issuer stays the only authority.
 */

const TOKEN = 'dev-user-token';
const MANDATE_KEY = 'mandate-id';

const MCC_NAMES: Record<string, string> = {
  '7995': 'Gambling',
  '6051': 'Quasi-cash',
  '4829': 'Money transfer',
  '5921': 'Liquor stores',
};

const STATUS_LABEL: Record<VirtualCard['status'], string> = { active: 'Active', frozen: 'Locked', used: 'Used', cancelled: 'Cancelled' };
const merchantName = (id: string) => id === 'wellcome' ? 'Wellcome' : id.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
const shortDate = (iso: string) => new Date(iso).toLocaleDateString('en-HK', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Hong_Kong' });

function Step({ n, title, done, children }: { n: number; title: string; done: boolean; children: ReactNode }) {
  return <section className="vc-step">
    <header><span className={`vc-num${done ? ' done' : ''}`}>{done ? <Check size={14} strokeWidth={2.5} /> : n}</span><h2>{title}</h2></header>
    <div className="vc-step-body">{children}</div>
  </section>;
}

export default function CardSetup() {
  const mandateId = localStorage.getItem(MANDATE_KEY) ?? '';
  const [mandate, setMandate] = useState<Mandate | null>(null);
  const [card, setCard] = useState<VirtualCard | null>(null);
  const [auths, setAuths] = useState<CardAuthorization[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!mandateId) { setLoaded(true); return; }
    try {
      const [m, c] = await Promise.all([api.mandate(TOKEN, mandateId), api.card(TOKEN, mandateId)]);
      setMandate(m); setCard(c);
      api.cardAuthorizations(TOKEN, mandateId).then((list) => setAuths(list.authorizations)).catch(() => setAuths([]));
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) setError(err instanceof Error ? err.message : 'Could not load the card.');
    } finally { setLoaded(true); }
  }, [mandateId]);

  useEffect(() => { void load(); }, [load]);

  const toggleLock = async () => {
    if (!card) return;
    setBusy(true); setError('');
    try {
      const result = card.status === 'frozen' ? await api.unfreezeCard(TOKEN, mandateId) : await api.freezeCard(TOKEN, mandateId);
      setCard(result.card);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The issuer did not accept the change.');
    } finally { setBusy(false); }
  };

  const lockable = card?.status === 'active' || card?.status === 'frozen';
  const controls = card?.controls;

  return <div className="vc">
    <nav className="vc-top">
      <a href="./" className="vc-back"><ArrowLeft size={16} />Mandate</a>
      <span className="vc-pill">Sandbox issuer</span>
    </nav>

    <h1>Virtual card</h1>
    <p className="vc-lede">A card the agent can spend with, limited to your rules. Every purchase gets its own single-use number.</p>

    {error && <p className="vc-error" role="alert">{error}</p>}

    {!loaded ? <p className="vc-muted">Loading…</p> : !card || !mandate ? (
      <div className="vc-empty">
        <p>Switch on an allowance first. The wallet issues its card automatically.</p>
        <a className="vc-button" href="./">Set up an allowance<ArrowRight size={16} /></a>
      </div>
    ) : <>
      <Step n={1} title="Card issued" done>
        <div className={`vc-card ${card.status}`}>
          <div className="vc-card-row"><span>Mandate</span><span className="vc-card-network">{card.network === 'visa' ? 'VISA' : 'mastercard'}</span></div>
          <div className="vc-card-number">•••• •••• •••• {card.last4}</div>
          <div className="vc-card-row"><span>Expires {String(card.exp_month).padStart(2, '0')}/{String(card.exp_year).slice(-2)}</span><span>{STATUS_LABEL[card.status]}</span></div>
        </div>
        <p className="vc-muted">Issued {shortDate(card.issued_at)}. The full number never leaves the issuer; shops only ever see a single-use card.</p>
      </Step>

      <Step n={2} title="Spending controls" done>
        <dl className="vc-rows">
          <div><dt>Per purchase</dt><dd>{money(controls!.spend_limit_minor)}</dd></div>
          {mandate.policy.period_limits.map((limit) => <div key={limit.period}><dt>{limit.period === 'calendar_week' ? 'Weekly' : 'Monthly'} budget</dt><dd>{money(limit.limit_minor)}</dd></div>)}
          <div><dt>Shops</dt><dd>{controls!.allowed_merchant_ids?.length ? controls!.allowed_merchant_ids.map(merchantName).join(', ') : 'Any'}</dd></div>
          <div><dt>Blocked</dt><dd>{controls!.blocked_mccs.map((mcc) => MCC_NAMES[mcc] ?? `MCC ${mcc}`).join(', ')}</dd></div>
          <div><dt>Valid until</dt><dd>{shortDate(controls!.expires_at)}</dd></div>
        </dl>
        <p className="vc-muted">Copied from the allowance. To change them, start a new allowance.</p>
      </Step>

      <Step n={3} title="Lock" done={card.status === 'active'}>
        <div className="vc-lock">
          <div>
            <b>{card.status === 'frozen' ? 'Card is locked' : card.status === 'active' ? 'Card is on' : `Card is ${STATUS_LABEL[card.status].toLowerCase()}`}</b>
            <span className="vc-muted">{lockable ? 'Locking declines every purchase until you unlock it.' : 'Revoked allowances cannot be unlocked.'}</span>
          </div>
          <button type="button" role="switch" aria-checked={card.status === 'active'} aria-label="Card on" className={`vc-switch${card.status === 'active' ? ' on' : ''}`}
            onClick={() => void toggleLock()} disabled={busy || !lockable}><i /></button>
        </div>
      </Step>

      <section className="vc-activity">
        <h2>Activity</h2>
        {auths.length ? <ul>{auths.slice(0, 8).map((a) => <li key={a.id}>
          <span><b>{merchantName(a.merchant_id)}</b><small>{shortDate(a.created_at)} · •••• {a.card_last4}</small></span>
          <span className={a.approved ? '' : 'vc-declined'}>{a.approved ? money(a.amount_minor) : 'Declined'}</span>
        </li>)}</ul> : <p className="vc-muted">No purchases yet.</p>}
        {card.single_use_cards.used + card.single_use_cards.active + card.single_use_cards.cancelled > 0 &&
          <p className="vc-muted">Single-use cards: {card.single_use_cards.used} used, {card.single_use_cards.active} open, {card.single_use_cards.cancelled} cancelled.</p>}
      </section>
    </>}
  </div>;
}
