import { ArrowRight, X } from 'lucide-react';
import type { Mandate } from '../../../../contracts/types';
import { categoryLabel, money } from '../lib/format';
import { possessive } from './holder';
import { storeName } from './Onboarding';
import StoresPanel from './StoresPanel';

/*
 * Profile: the user's store accounts and the current allowance.
 * Store connections can change any time. Rules can't be edited in place: a confirmed mandate is fixed,
 * so "Change rules" sets up a new allowance and the old one is revoked once the new one is confirmed.
 */

type Props = {
  token: string;
  online: boolean | null;
  mandate: Mandate | null;
  /** Who the allowance is for; empty when it's the user's own. */
  holder: string;
  onChangeRules: () => void;
  onClose: () => void;
};

export default function ProfileSheet({ token, online, mandate, holder, onChangeRules, onClose }: Props) {
  const policy = mandate?.policy;
  const active = mandate?.status === 'active';
  return <div className="m2-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="m2-modal ob-profile" role="dialog" aria-modal="true" aria-labelledby="profile-title">
      <header className="m2-modal-head">
        <div><p className="m2-kicker">Your account</p><h2 id="profile-title">Profile</h2></div>
        <button className="m2-icon-button" onClick={onClose} aria-label="Close profile"><X size={20} /></button>
      </header>

      <h3 className="ob-section">Store accounts</h3>
      <p className="m2-muted ob-small">Kumi fills the real cart only at connected stores your allowance includes. It never checks out.</p>
      <StoresPanel token={token} online={online} allowedIds={active ? policy?.allowed_merchant_ids : []} />

      <h3 className="ob-section">{possessive(holder)} allowance</h3>
      {policy ? <>
        <dl className="ob-rules">
          <div><dt>Status</dt><dd>{active ? 'Active' : mandate?.status === 'expired' ? 'Expired' : 'Frozen'}</dd></div>
          {policy.period_limits.map((p) => <div key={p.period}><dt>{p.period === 'calendar_month' ? 'Monthly budget' : 'Weekly budget'}</dt><dd>{money(p.limit_minor)}</dd></div>)}
          <div><dt>Most per order</dt><dd>{money(policy.per_order_limit_minor)}</dd></div>
          <div><dt>{policy.allowed_merchant_ids.length > 1 ? 'Shops' : 'Shop'}</dt><dd>{policy.allowed_merchant_ids.map(storeName).join(', ')}</dd></div>
          <div><dt>Never buy</dt><dd>{policy.blocked_categories.map(categoryLabel).join(', ') || 'Nothing blocked'}</dd></div>
          {policy.approval_above_minor != null && <div><dt>Ask me first above</dt><dd>{money(policy.approval_above_minor)}</dd></div>}
          {policy.risk_review && <div><dt>Extra protection</dt><dd>Unusual purchases reviewed</dd></div>}
          <div><dt>Ends</dt><dd>{new Date(policy.expires_at).toLocaleDateString('en-HK', { day: 'numeric', month: 'short', year: 'numeric' })}</dd></div>
        </dl>
        <button className="m2-ghost ob-change" onClick={onChangeRules} disabled={online === false}>{active ? 'Change rules' : 'Start a new allowance'}<ArrowRight size={16} /></button>
        {active && <p className="m2-muted ob-small">Rules can’t be edited in place. You’ll set up a new allowance; the current one is frozen once the new one is switched on, and any held money is released.</p>}
      </> : <p className="m2-muted">No allowance yet.</p>}
    </section>
  </div>;
}
