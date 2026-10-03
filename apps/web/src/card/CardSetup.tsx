import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Snowflake, Wifi } from 'lucide-react';
import '../simple/openai-tokens.css';
import ReactiveCharacter from '../components/ReactiveCharacter';
import type { CardAuthorization, Mandate, VirtualCard } from '../../../../contracts/types';
import { api, ApiError } from '../lib/api';
import { money } from '../lib/format';

/*
 * Virtual card setup, in the simple flow's look: the wallet issues the card when an allowance is confirmed, so this
 * page shows what Kip was issued, the controls the issuer enforces, and the freeze switch. The issuer decides.
 */

const TOKEN = 'dev-user-token';
const MANDATE_KEY = 'mandate-id';

const MCC_NAMES: Record<string, string> = {
  '7995': 'Gambling',
  '6051': 'Quasi-cash',
  '4829': 'Money transfer',
  '5921': 'Liquor stores',
};

const STATUS_LABEL: Record<VirtualCard['status'], string> = { active: 'Active', frozen: 'Paused', used: 'Used', cancelled: 'Cancelled' };
const hkd = (minor: number) => money(minor).replace('HK$', 'HK$ ');
const merchantName = (id: string) => id === 'wellcome' ? 'Wellcome' : id.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
const shortDate = (iso: string) => new Date(iso).toLocaleDateString('en-HK', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Hong_Kong' });

function Sticker({ name, state, size = 88, tilt = -6 }: { name: 'kip' | 'stella'; state: string; size?: number; tilt?: number }) {
  return <span className={`m2-sticker ${name}`} style={{ width: size, height: size, transform: `rotate(${tilt}deg)` }}><ReactiveCharacter name={name} state={state} size={size} /></span>;
}

export default function CardSetup() {
  const mandateId = localStorage.getItem(MANDATE_KEY) ?? '';
  const [online, setOnline] = useState<boolean | null>(null);
  const [mandate, setMandate] = useState<Mandate | null>(null);
  const [card, setCard] = useState<VirtualCard | null>(null);
  const [auths, setAuths] = useState<CardAuthorization[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try { await api.health(); setOnline(true); } catch { setOnline(false); }
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

  const toggleFreeze = async () => {
    if (!card) return;
    setBusy(true); setError('');
    try {
      const action = card.status === 'frozen' ? 'unfreeze' : 'freeze';
      const result = action === 'unfreeze' ? await api.unfreezeCard(TOKEN, mandateId) : await api.freezeCard(TOKEN, mandateId);
      sessionStorage.removeItem(`mandate-idempotency-${action}-${mandateId}`);
      setCard(result.card);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The issuer did not accept the change.');
    } finally { setBusy(false); }
  };

  const expiry = card ? `${String(card.exp_month).padStart(2, '0')}/${String(card.exp_year).slice(-2)}` : '';
  const singleUse = card ? card.single_use_cards.used + card.single_use_cards.active + card.single_use_cards.cancelled : 0;
  const shops = card?.controls.allowed_merchant_ids ?? [];

  return <div className="m2">
    <header className="m2-top">
      <a className="m2-brand m2-back" href="./"><ArrowLeft size={18} />Mandate</a>
      <span className={`m2-pill ${online === false ? 'off' : ''}`}><i />{online === false ? 'Wallet offline' : 'Sandbox, no real money'}</span>
    </header>

    {error && <div className="m2-error" role="alert"><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss">Dismiss</button></div>}

    {!loaded ? <div className="m2-loading"><ReactiveCharacter className="m2-avatar" name="kip" state="idle" size={96} /></div> : !card || !mandate ? (
      <div className="m2-panel m2-center">
        <Sticker name="kip" state="idle" size={150} tilt={-4} />
        <h2>No card yet.</h2>
        <p className="m2-muted">Kip issues a virtual card the moment you switch on an allowance.</p>
        <a className="m2-cta" href="./">Set up an allowance<ArrowRight size={18} /></a>
      </div>
    ) : (
      <section className="m2-setup">
        <div className="m2-col">
          <div>
            <p className="m2-kicker">Kip’s virtual card</p>
            <h1>One card, <em>your rules on it.</em></h1>
            <p className="m2-lede">Kumi never sees this number. Each purchase gets its own single-use card, locked to the shop and the amount.</p>
          </div>
          <div className={`m2-vcard ${card.status}`} role="img"
            aria-label={`${card.network === 'mastercard' ? 'Mastercard' : 'Visa'} ending ${card.last4}, expires ${expiry}, ${STATUS_LABEL[card.status]}`}>
            <div className="m2-vcard-top"><b>Mandate</b>{card.status !== 'active' && <span>{STATUS_LABEL[card.status]}</span>}</div>
            <div className="m2-vcard-chip"><i /><Wifi size={22} strokeWidth={1.75} /></div>
            <div className="m2-vcard-number">•••• •••• •••• {card.last4}</div>
            <div className="m2-vcard-foot">
              <span><small>Card holder</small>Mum’s groceries</span>
              <span><small>Valid thru</small>{expiry}</span>
              <b>{card.network === 'mastercard' ? 'mastercard' : 'VISA'}</b>
            </div>
          </div>
          <p className="m2-vcard-meta">Issued {shortDate(card.issued_at)} · {singleUse} single-use {singleUse === 1 ? 'card' : 'cards'} so far</p>
          {card.status === 'frozen'
            ? <button className="m2-cta" onClick={() => void toggleFreeze()} disabled={busy}><Snowflake size={18} />{busy ? 'Resuming…' : 'Unfreeze the card'}</button>
            : card.status === 'active'
              ? <button className="m2-freeze" onClick={() => void toggleFreeze()} disabled={busy}><Snowflake size={18} />{busy ? 'Freezing…' : 'Freeze the card'}</button>
              : <a className="m2-cta" href="./">Start a new allowance<ArrowRight size={18} /></a>}
        </div>

        <div className="m2-col">
          <div className="m2-setup-form">
            <h2>What the card allows</h2>
            {mandate.policy.period_limits.map((limit) => <div key={limit.period} className="m2-ledger-row fixed"><span>{limit.period === 'calendar_week' ? 'Weekly' : 'Monthly'} budget</span><b>{hkd(limit.limit_minor)}</b></div>)}
            <div className="m2-ledger-row fixed"><span>Most per purchase</span><b>{hkd(card.controls.spend_limit_minor)}</b></div>
            <div className="m2-ledger-row fixed"><span>{shops.length > 1 ? 'Shops' : 'Shop'}</span><b>{shops.length ? shops.map(merchantName).join(', ') : 'Any'}</b></div>
            <div className="m2-ledger-row fixed"><span>Always declined</span><b>{card.controls.blocked_mccs.map((mcc) => MCC_NAMES[mcc] ?? `MCC ${mcc}`).join(', ')}</b></div>
            <div className="m2-ledger-row fixed"><span>Valid until</span><b>{shortDate(card.controls.expires_at)}</b></div>
            <p className="m2-muted m2-card-hint">Copied from the allowance. To change them, start a new allowance.</p>
          </div>

          <div className="m2-feed">
            <div className="m2-feed-head"><Sticker name="stella" state={auths.length ? 'pass' : 'idle'} size={46} tilt={-8} /><div><strong>Card activity</strong><small>Every approval and decline from the issuer</small></div></div>
            {auths.length ? <ol>{auths.slice(0, 10).map((a) => <li key={a.id} className={a.approved ? 'good' : 'bad'}>
              <time>{new Date(a.created_at).toLocaleDateString('en-HK', { day: 'numeric', month: 'short', timeZone: 'Asia/Hong_Kong' })}</time>
              <span className="m2-dot kip" />
              <span>{merchantName(a.merchant_id)} · ···· {a.card_last4} · {a.approved ? hkd(a.amount_minor) : `Declined: ${a.message}`}</span>
            </li>)}</ol> : <p className="m2-empty">Nothing yet. Send Kumi shopping.</p>}
          </div>
        </div>
      </section>
    )}
  </div>;
}
