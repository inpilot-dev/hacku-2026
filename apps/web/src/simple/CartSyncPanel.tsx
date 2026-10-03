import { useState } from 'react';
import { ShoppingCart } from 'lucide-react';
import type { CartSyncResult, Quote } from '../../../../contracts/types';
import { api } from '../lib/api';
import { money } from '../lib/format';
import { storeName } from './Onboarding';
import StoreLoginCanvas from './StoreLoginCanvas';

/*
 * Puts the quoted basket into the user's real store cart and shows how the store's cart compares, line by line.
 * Kumi never checks out. A cart that differs from the quote, or holds other selected items, is flagged as not
 * ready for checkout; the store's total is shown as what the store says, never as a new quote.
 */

const LINE_TEXT: Record<CartSyncResult['lines'][number]['status'], string> = {
  ok: 'In cart',
  price_changed: 'Price changed',
  quantity_mismatch: 'Quantity differs',
  missing: 'Not in cart',
};

export default function CartSyncPanel({ token, mandateId, quote }: { token: string; mandateId: string; quote: Quote }) {
  const name = storeName(quote.merchant_id);
  const [result, setResult] = useState<CartSyncResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [signingIn, setSigningIn] = useState(false);

  async function sync() {
    setBusy(true); setError('');
    try {
      setResult(await api.syncCart(token, { mandate_id: mandateId, quote_id: quote.id }));
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not reach your ${name} cart.`);
    } finally { setBusy(false); }
  }

  async function connect() {
    setError('');
    try { await api.connectStore(token, quote.merchant_id); setSigningIn(true); } catch (err) {
      setError(err instanceof Error ? err.message : `Could not open ${name}.`);
    }
  }

  if (signingIn) {
    return <div className="cs">
      <StoreLoginCanvas token={token} storeId={quote.merchant_id} storeName={name} onCancel={() => setSigningIn(false)}
        onFinished={(status) => { setSigningIn(false); if (status === 'connected') void sync(); else setError(`${name} sign-in didn’t finish.`); }} />
    </div>;
  }

  const needsSignIn = result?.status === 'not_connected' || result?.status === 'session_expired';
  return <div className="cs">
    {!result || needsSignIn ? <>
      <button className="m2-ghost cs-go" onClick={() => void (needsSignIn ? connect() : sync())} disabled={busy}>
        <ShoppingCart size={16} />{busy ? `Filling your ${name} cart…` : needsSignIn ? `Sign in to ${name}` : `Put it in my ${name} cart`}
      </button>
      {needsSignIn && <p className="m2-muted cs-note">{result!.message}</p>}
    </> : <div className={`cs-result ${result.checkout_ready ? 'ready' : 'warn'}`}>
      <p className="cs-head">{result.checkout_ready ? `Your ${name} cart matches.` : `In your ${name} cart, but check before paying.`}</p>
      <ul>
        {result.lines.map((line) => <li key={line.sku} className={line.status}>
          <span>{line.cart_quantity}× {line.title}</span>
          <small>{line.status === 'price_changed' && line.cart_unit_price_minor != null
            ? `${money(line.quoted_unit_price_minor)} → ${money(line.cart_unit_price_minor)}` : LINE_TEXT[line.status]}</small>
        </li>)}
        {result.other_items.filter((item) => item.checked).map((item) => <li key={`o${item.sku}`} className="other">
          <span>{item.quantity}× {item.title}</span><small>Already in cart</small>
        </li>)}
      </ul>
      {result.cart_subtotal_minor != null && result.cart_subtotal_minor !== result.quote_subtotal_minor &&
        <p className="cs-note">{name} shows {money(result.cart_subtotal_minor)} for these items; Kip’s quote is {money(result.quote_subtotal_minor)}.</p>}
      <p className="m2-muted cs-note">{result.message}</p>
      <button className="m2-link" onClick={() => void sync()} disabled={busy}>{busy ? 'Checking…' : 'Check the cart again'}</button>
    </div>}
    {error && <p className="cs-error" role="alert">{error}</p>}
  </div>;
}
