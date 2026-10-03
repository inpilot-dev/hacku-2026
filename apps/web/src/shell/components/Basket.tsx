import { useState } from 'react';
import { CheckCircle2, ExternalLink, ReceiptText, ShoppingCart, XCircle } from 'lucide-react';
import type { CartSyncResult, PaymentOptionsResponse, Quote, RiskAssessment } from '../../../../../contracts/types';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { api } from '@/lib/api';
import { categoryLabel, money, periodWord } from '@/lib/format';
import { cn } from '@/lib/utils';
import StoreLoginCanvas from '@/simple/StoreLoginCanvas';
import { TOKEN, useAccount } from '../data/account';
import { reasonTitle, riskCheck, type Groceries } from '../data/useGroceries';
import { storeName } from './AllowanceSetup';
import { Sticker, Working } from './chat';
import './store-login.css';

/* The priced basket and what the wallet decided about it. */

export default function Basket({ g }: { g: Groceries }) {
  const account = useAccount();
  const quote = g.quote!;
  const blocked = new Set<string>(account.mandate?.policy.blocked_categories ?? []);
  const v = g.phase === 'verdict' ? g.verdict : null;
  const per = periodWord(account.mandate?.policy.period_limits[0]?.period);

  return <Card className={cn(v?.kind === 'paid' && 'border-success/40', v?.kind === 'refused' && 'border-destructive/40')}>
    <CardHeader>
      <CardDescription>{storeName(quote.merchant_id)} · Click &amp; Collect</CardDescription>
      <CardAction><Sticker who={v || g.phase === 'paying' ? 'kip' : 'kumi'} size={56}
        state={v?.kind === 'paid' ? 'approved' : v?.kind === 'refused' ? 'refused' : v || g.phase === 'paying' ? 'idle' : 'happy'} /></CardAction>
      <CardTitle className="text-lg">{v ? verdictTitle(v.kind, quote.total_minor, v.kind === 'paid' ? v.receipt.amount_minor : 0) : g.phase === 'paying' ? 'Checking your rules…' : 'Basket ready'}</CardTitle>
    </CardHeader>
    <CardContent className="space-y-4 text-sm">
      <ul className="space-y-1.5">
        {quote.items.map((item) => { const category = g.catalogById.get(item.product_id)?.category ?? ''; const flagged = blocked.has(category);
          return <li key={item.product_id} className="flex justify-between gap-4">
            <span className={cn(flagged && 'text-destructive')}>{item.quantity}× {item.title}{flagged && <Badge variant="outline" className="ml-2 border-destructive/40 text-destructive">{categoryLabel(category)}</Badge>}</span>
            <span className="tabular-nums">{money(item.line_total_minor)}</span></li>; })}
        {quote.charges.map((c, i) => <li key={`c${i}`} className="flex justify-between gap-4 text-muted-foreground"><span>{c.label}</span><span>{c.amount_minor ? money(c.amount_minor) : 'Free'}</span></li>)}
      </ul>
      <div className="flex justify-between border-t pt-3 font-semibold"><span>Total</span><span className="tabular-nums">{money(quote.total_minor)}</span></div>

      {g.phase === 'basket' && <>
        {g.ruleTestBasket
          ? <p className="text-xs text-muted-foreground" role="status">Rule test only: the agent did not build this basket. It shows the wallet’s checks and can’t go into a store cart.</p>
          : <CartSync key={quote.id} quote={quote} />}
        {g.paymentComparison && <PaymentRoutes comparison={g.paymentComparison} selected={g.routeId} onSelect={g.selectRoute} />}
      </>}

      {g.phase === 'paying' && <Working>Kip is checking your rules and paying…</Working>}

      {v?.kind === 'paid' && <p className="text-muted-foreground">Paid in the sandbox within your rules. {money(account.available)} left this {per}. This is not a retailer order confirmation.</p>}
      {v?.kind === 'refused' && (v.violations.length
        ? <ul className="space-y-2">{v.violations.map((x, i) => <li key={`${x.rule_id}-${i}`} className="flex gap-2"><XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
          <span><span className="font-medium">{reasonTitle(x)}</span><span className="block text-xs text-muted-foreground">{x.message}</span></span></li>)}</ul>
        : <p className="text-muted-foreground">{v.message}</p>)}
      {v?.kind === 'uncertain' && <Alert><AlertTitle>Payment status unknown</AlertTitle><AlertDescription>{v.message}</AlertDescription></Alert>}
      {v?.kind === 'review' && <div className="space-y-3">
        <p className="text-muted-foreground">Kip paused this order for you. Nothing is reserved or paid while it waits.</p>
        {v.risk && v.risk.score > 0 && <RiskMeter risk={v.risk} />}
        {v.violations.length > 0 && <ul className="space-y-2">{v.violations.map((x, i) => { const pts = v.risk?.signals.find((s) => s.check === riskCheck(x))?.points;
          return <li key={`${x.rule_id}-${i}`}><span className="font-medium">{reasonTitle(x)}</span>{pts ? <Badge variant="secondary" className="ml-2">+{pts}</Badge> : null}
            <span className="block text-xs text-muted-foreground">{x.message}</span></li>; })}</ul>}
        <p className="text-xs text-muted-foreground">Approval covers only this basket, once. Expires {new Date(v.approval.expires_at).toLocaleString('en-HK', { timeZone: 'Asia/Hong_Kong', dateStyle: 'medium', timeStyle: 'short' })} HKT.</p>
      </div>}
    </CardContent>
    <CardFooter className="flex-col items-stretch gap-2 sm:flex-row sm:flex-wrap">
      {g.phase === 'basket' && <>
        <Button className="min-h-11 flex-1" disabled={Boolean(g.paymentComparison && !g.routeId)} onClick={() => void g.checkout()}>Pay {money(quote.total_minor)} in the sandbox</Button>
        <Button variant="ghost" className="min-h-11" onClick={g.resetShop}>Start over</Button>
      </>}
      {v?.kind === 'review' && <>
        <Button className="min-h-11 flex-1" onClick={() => void g.decide(true)} disabled={g.busy === 'decide'}>Approve once</Button>
        <Button variant="outline" className="min-h-11" onClick={() => void g.decide(false)} disabled={g.busy === 'decide'}>Say no</Button>
      </>}
      {v?.kind === 'uncertain' && <Button className="min-h-11 flex-1" disabled={Boolean(g.busy)} onClick={() => void g.checkPaymentStatus()}>{g.busy === 'payment-status' ? 'Checking…' : 'Check payment status'}</Button>}
      {v?.kind === 'paid' && <Button variant="outline" className="min-h-11" onClick={() => void account.receipts.open({ receipt: v.receipt, quote, occurredAt: v.receipt.paid_at })}><ReceiptText />Receipt</Button>}
      {v && v.kind !== 'review' && v.kind !== 'uncertain' && <Button className="min-h-11 flex-1" onClick={g.resetShop}>Shop again</Button>}
    </CardFooter>
    {g.phase === 'basket' && g.routeLabel && <p className="px-6 text-xs text-muted-foreground">Paying via {g.routeLabel}. The wallet checks your permission again before paying.</p>}
  </Card>;
}

function verdictTitle(kind: string, total: number, paid: number) {
  if (kind === 'paid') return `Paid ${money(paid)}`;
  if (kind === 'refused') return 'Refused. Nothing was paid.';
  if (kind === 'review') return `Approve ${money(total)}?`;
  return 'Still checking';
}

function RiskMeter({ risk }: { risk: RiskAssessment }) {
  const scale = Math.max(100, risk.score, risk.threshold);
  return <div className="space-y-1.5" role="img" aria-label={`Risk score ${risk.score}; purchases at ${risk.threshold} or more wait for you`}>
    <div className="flex justify-between text-xs"><span className="font-medium">Risk score {risk.score}</span><span className="text-muted-foreground">reviews from {risk.threshold}</span></div>
    <div className="relative h-2 rounded-full bg-muted">
      <div className="h-2 rounded-full bg-warning" style={{ width: `${(risk.score / scale) * 100}%` }} />
      <div className="absolute -top-1 h-4 w-0.5 bg-foreground" style={{ left: `${(risk.threshold / scale) * 100}%` }} />
    </div>
  </div>;
}

function sourceUrl(value: string) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
}

function PaymentRoutes({ comparison, selected, onSelect }: { comparison: PaymentOptionsResponse; selected: string | null; onSelect: (id: string, label: string) => void }) {
  return <fieldset className="space-y-2">
    <legend className="mb-1 font-medium">Payment route</legend>
    <p className="text-xs text-muted-foreground">Compare fees and estimated rewards.</p>
    <RadioGroup value={selected ?? ''} onValueChange={(id) => { const o = comparison.options.find((x) => x.route_id === id); if (o) onSelect(o.route_id, o.label); }}>
      {comparison.options.map((o) => <Label key={o.route_id} htmlFor={`route-${o.route_id}`}
        className={cn('flex cursor-pointer items-start gap-3 rounded-lg border p-3 font-normal', selected === o.route_id && 'border-primary', !o.eligible && 'cursor-not-allowed opacity-60')}>
        <RadioGroupItem id={`route-${o.route_id}`} value={o.route_id} disabled={!o.eligible} className="mt-0.5" />
        <span className="grid gap-0.5 text-sm">
          <span className="font-medium">{o.label.replace(/\s*\([^)]*\)/g, '')}{o.route_id === comparison.recommended_route_id && o.eligible && <Badge variant="secondary" className="ml-2">Recommended</Badge>}</span>
          {o.eligible ? <span className="text-xs text-muted-foreground">Charge {money(o.gross_minor)} · fee {money(o.fee_minor)} · reward ~{money(o.reward_minor)} · net {money(o.net_minor)}</span>
            : <span className="text-xs text-muted-foreground">{o.ineligible_reason || 'Unavailable for this purchase'}</span>}

        </span>
      </Label>)}
    </RadioGroup>
    <details className="text-xs text-muted-foreground"><summary className="inline-flex min-h-10 cursor-pointer items-center">Fees, rewards & sources</summary>
      <p>{comparison.rule}</p>
      {comparison.options.map((o) => <div key={o.route_id} className="mt-3"><p className="font-medium">{o.label}</p>{o.caveats.map((text, i) => <p key={i} className="mt-1">{text}</p>)}</div>)}
      <p className="mt-1">Evaluated {new Date(comparison.evaluated_at).toLocaleString('en-HK')}. Rewards are estimates, not a reduction in the charge.</p>
      {comparison.evidence.map((s) => { const url = sourceUrl(s.url); return <p key={s.id} className="mt-1">{url ? <a href={url} target="_blank" rel="noopener noreferrer" className="underline">{s.title}<ExternalLink className="ml-1 inline size-3" /></a> : s.title}: {s.quote}</p>; })}
    </details>
  </fieldset>;
}

const LINE_TEXT: Record<CartSyncResult['lines'][number]['status'], string> = { ok: 'In cart', price_changed: 'Price changed', quantity_mismatch: 'Quantity differs', missing: 'Not in cart' };

/** Puts the basket into the user's real store cart (never checks out) and compares it line by line. */
function CartSync({ quote }: { quote: Quote }) {
  const account = useAccount();
  const name = storeName(quote.merchant_id);
  const [result, setResult] = useState<CartSyncResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [signingIn, setSigningIn] = useState(false);

  async function sync() {
    if (!account.mandate) return;
    setBusy(true); setError('');
    try { setResult(await api.syncCart(TOKEN, { mandate_id: account.mandate.id, quote_id: quote.id })); } catch (err) {
      setError(err instanceof Error ? err.message : `Could not reach your ${name} cart.`);
    } finally { setBusy(false); }
  }
  async function connect() {
    setError('');
    try { await api.connectStore(TOKEN, quote.merchant_id); setSigningIn(true); } catch (err) { setError(err instanceof Error ? err.message : `Could not open ${name}.`); }
  }

  if (signingIn) return <div className="shell-store-login"><StoreLoginCanvas token={TOKEN} storeId={quote.merchant_id} storeName={name} onCancel={() => setSigningIn(false)}
    onFinished={(status) => { setSigningIn(false); if (status === 'connected') void sync(); else setError(`${name} sign-in didn’t finish.`); }} /></div>;

  const needsSignIn = result?.status === 'not_connected' || result?.status === 'session_expired';
  return <div className="space-y-2">
    {!result || needsSignIn ? <>
      <Button variant="outline" size="sm" onClick={() => void (needsSignIn ? connect() : sync())} disabled={busy}>
        <ShoppingCart />{busy ? `Filling your ${name} cart…` : needsSignIn ? `Sign in to ${name}` : `Put it in my ${name} cart`}</Button>
      {needsSignIn && <p className="text-xs text-muted-foreground">{result!.message}</p>}
    </> : <div className={cn('space-y-2 rounded-lg border p-3', result.checkout_ready ? 'border-success/40' : 'border-warning/60')}>
      <p className="flex items-center gap-2 font-medium">{result.checkout_ready ? <CheckCircle2 className="size-4 text-success" /> : null}
        {result.checkout_ready ? `Your ${name} cart matches.` : `In your ${name} cart, but check before paying.`}</p>
      <ul className="space-y-1 text-xs">
        {result.lines.map((line) => <li key={line.sku} className="flex justify-between gap-3"><span>{line.cart_quantity}× {line.title}</span>
          <span className={cn(line.status !== 'ok' && 'text-warning')}>{line.status === 'price_changed' && line.cart_unit_price_minor != null ? `${money(line.quoted_unit_price_minor)} → ${money(line.cart_unit_price_minor)}` : LINE_TEXT[line.status]}</span></li>)}
        {result.other_items.filter((i) => i.checked).map((i) => <li key={`o${i.sku}`} className="flex justify-between gap-3 text-muted-foreground"><span>{i.quantity}× {i.title}</span><span>Already in cart</span></li>)}
      </ul>
      {result.cart_subtotal_minor != null && result.cart_subtotal_minor !== result.quote_subtotal_minor &&
        <p className="text-xs">{name} shows {money(result.cart_subtotal_minor)} for these items; the wallet’s quote is {money(result.quote_subtotal_minor)}.</p>}
      <p className="text-xs text-muted-foreground">{result.message}</p>
      <Button variant="ghost" size="sm" onClick={() => void sync()} disabled={busy}>{busy ? 'Checking…' : 'Check the cart again'}</Button>
    </div>}
    {error && <p className="text-xs text-destructive" role="alert">{error}</p>}
  </div>;
}
