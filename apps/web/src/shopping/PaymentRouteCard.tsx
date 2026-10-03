import type { PaymentOptionsResponse } from '../../../../contracts/types';
import { money } from '../lib/format';

function sourceUrl(value: string) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
}

export default function PaymentRouteCard({ comparison, selected, onSelect }: {
  comparison: PaymentOptionsResponse;
  selected: string | null;
  onSelect: (id: string, label: string) => void;
}) {
  return <fieldset className="conversation-routes">
    <legend>Choose a payment route</legend>
    <p className="m2-muted">{comparison.rule}</p>
    {comparison.options.map((option) => <label key={option.route_id} className={`conversation-route${selected === option.route_id ? ' selected' : ''}`}>
      <input type="radio" name="payment-route" checked={selected === option.route_id} disabled={!option.eligible} onChange={() => onSelect(option.route_id, option.label)} />
      <span><strong>{option.label}</strong>{option.route_id === comparison.recommended_route_id && option.eligible && <small>Recommended by the wallet</small>}
        {option.eligible ? <><small>Charge {money(option.gross_minor)} · fee {money(option.fee_minor)} · estimated reward {money(option.reward_minor)}</small><b>Estimated net cost {money(option.net_minor)}</b></> : <small>{option.ineligible_reason || 'Unavailable for this purchase'}</small>}
        {option.caveats.map((text, i) => <small key={i}>{text}</small>)}
      </span>
    </label>)}
    <details><summary>Sources and conditions</summary>
      <p>Evaluated {new Date(comparison.evaluated_at).toLocaleString('en-HK')}. Rewards are estimates, not a reduction in the amount charged.</p>
      {comparison.evidence.map((source) => { const url = sourceUrl(source.url); return <div key={source.id}>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{source.title}</a> : <b>{source.title}</b>}<small>Observed {new Date(source.observed_at).toLocaleString('en-HK')}</small><p>{source.quote}</p></div>; })}
    </details>
  </fieldset>;
}
