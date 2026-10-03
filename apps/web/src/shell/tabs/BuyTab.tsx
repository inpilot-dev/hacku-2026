import { useEffect, useRef, useState } from 'react';
import { ArrowUp, CheckCircle2, ExternalLink, ListChecks, MapPin, Store, XCircle } from 'lucide-react';
import type { Purchase } from '../../../../../contracts/types';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { Textarea } from '@/components/ui/textarea';
import { money } from '@/lib/format';
import { Bubble, Linkified, PageHeader, shortUrl, Working } from '../components/chat';
import { isWorking, usePurchases } from '../data/usePurchases';

/*
 * One-time purchases: type what you want, the agent finds the best deal on the web, takes it through the shop's
 * guest checkout and shows the exact total. Nothing is paid until you approve that total.
 */

const SUGGESTIONS = [
  'A 65W USB-C GaN charger with at least 2 USB-C ports, under HK$400',
  'Noise-cancelling wireless earbuds under HK$600',
  'A phone with at least 8GB RAM and a 1080p screen, under HK$3000',
];

const STATUS_TEXT: Partial<Record<Purchase['status'], string>> = {
  queued: 'Starting…',
  searching: 'Searching shops…',
  checking_out: 'Checking out as a guest…',
  paying: 'Paying at the shop…',
};

export default function BuyTab({ onOpenProfile }: { onOpenProfile: () => void }) {
  const p = usePurchases();
  const [text, setText] = useState('');
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [p.purchases]);

  async function send(value = text) {
    if (await p.start(value)) setText('');
  }

  return <div className="flex flex-col">
    <PageHeader title="Buy anything" description="Describe it. The agent finds the best deal, checks out as a guest and waits for your OK."
      action={p.purchases.length > 0 && !p.busy ? <Button variant="ghost" size="sm" onClick={p.clear}>Clear</Button> : undefined} />

    {p.purchases.length === 0 && <div className="mb-6 grid gap-2">
      <p className="text-sm text-muted-foreground">Try</p>
      {SUGGESTIONS.map((s) => <button key={s} onClick={() => setText(s)}
        className="min-h-11 rounded-xl border bg-card px-4 py-3 text-left text-sm transition-colors hover:bg-accent">{s}</button>)}
    </div>}

    <div className="space-y-6">
      {p.purchases.map((purchase) => <Thread key={purchase.id} purchase={purchase} acting={p.acting === purchase.id}
        onApprove={() => void p.approve(purchase)} onCancel={() => void p.cancel(purchase)} />)}
      <div ref={end} />
    </div>

    {p.needsProfile && <Alert className="mt-6">
      <MapPin />
      <AlertTitle>Add your delivery details first</AlertTitle>
      <AlertDescription>
        <p>Shops need a name, phone and address for the guest checkout.</p>
        <Button size="sm" className="mt-2" onClick={onOpenProfile}>Add delivery details</Button>
      </AlertDescription>
    </Alert>}
    {p.error && <Alert variant="destructive" className="mt-6"><XCircle /><AlertDescription>{p.error}</AlertDescription></Alert>}

    {/* The composer sits above the phone tab bar and at the bottom of the page on desktop. */}
    <div className="sticky bottom-20 z-10 mt-6 md:bottom-4">
      <form className="relative rounded-2xl border bg-card shadow-sm focus-within:ring-2 focus-within:ring-ring/40"
        onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={1000}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }}
          placeholder={p.busy ? 'Finish or cancel the current purchase first' : 'What do you want to buy?'}
          aria-label="What do you want to buy?" disabled={p.sending || p.busy}
          className="min-h-14 resize-none border-0 bg-transparent pr-14 shadow-none focus-visible:ring-0" />
        <Button type="submit" size="icon" className="absolute right-2 bottom-2 size-10 rounded-full"
          disabled={!text.trim() || p.sending || p.busy} aria-label="Send"><ArrowUp /></Button>
      </form>
    </div>
  </div>;
}

function Thread({ purchase, acting, onApprove, onCancel }: { purchase: Purchase; acting: boolean; onApprove: () => void; onCancel: () => void }) {
  const working = isWorking(purchase);
  const steps = purchase.events;
  return <section className="space-y-3" aria-label={`Purchase: ${purchase.request}`}>
    <Bubble from="you">{purchase.request}</Bubble>

    {purchase.spec && <div className="flex flex-wrap gap-1.5 pl-1">
      <Badge variant="secondary">{purchase.spec.item}</Badge>
      {purchase.spec.max_price_minor != null && <Badge variant="secondary">under {money(purchase.spec.max_price_minor)}</Badge>}
      {purchase.spec.requirements.map((r) => <Badge key={r} variant="outline" className="font-normal">{r}</Badge>)}
      {purchase.spec.preference && <Badge variant="outline" className="font-normal">prefers {purchase.spec.preference}</Badge>}
    </div>}

    {working && <div className="space-y-2 pl-1">
      <Working>{STATUS_TEXT[purchase.status] ?? 'Working…'}</Working>
      {steps.length > 0 && <p className="text-xs text-muted-foreground line-clamp-2">{steps[steps.length - 1].text}</p>}
    </div>}

    {!working && purchase.status !== 'awaiting_approval' && <Bubble from="agent" tone={tone(purchase)}><Linkified text={purchase.message} /></Bubble>}

    {purchase.status === 'awaiting_approval' && purchase.order && purchase.choice && <Card className="gap-4">
      <CardHeader>
        <CardDescription className="flex items-center gap-1.5"><Store className="size-3.5" />{purchase.order.shop}</CardDescription>
        <CardTitle className="text-base leading-snug">{purchase.choice.title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex items-baseline justify-between"><span className="text-muted-foreground">Total at checkout</span>
          <span className="text-xl font-semibold tabular-nums">{purchase.order.total_text ?? money(purchase.order.total_minor)}</span></div>
        <div className="flex justify-between text-muted-foreground"><span>Delivery</span><span>{purchase.order.shipping_text ?? 'not shown'}</span></div>
        {purchase.choice.checks.length > 0 && <ul className="space-y-1">
          {purchase.choice.checks.map((c) => <li key={c.requirement} className="flex items-start gap-2">
            {c.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /> : <ListChecks className="mt-0.5 size-4 shrink-0 text-warning" />}
            <span>{c.requirement}{c.ok ? '' : ': the shop’s page does not say'}</span>
          </li>)}
        </ul>}
        <AccountOnly options={purchase.options} />
        <a href={purchase.choice.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-muted-foreground underline underline-offset-2">
          View the product page<ExternalLink className="size-3" /></a>
      </CardContent>
      <CardFooter className="flex-col items-stretch gap-2 sm:flex-row">
        <Button className="min-h-11 flex-1" onClick={onApprove} disabled={acting}>
          {acting ? 'Paying…' : `Approve and pay ${purchase.order.total_text ?? money(purchase.order.total_minor)}`}</Button>
        <Button variant="outline" className="min-h-11" onClick={onCancel} disabled={acting}>Cancel</Button>
      </CardFooter>
      {!purchase.live_payments && <p className="px-6 text-xs text-muted-foreground">Sandbox: the card is filled in but the order is not placed.</p>}
    </Card>}

    {purchase.status === 'needs_account' && <AccountOnly options={purchase.options} prominent />}

    {steps.length > 0 && !working && <details className="pl-1 text-xs text-muted-foreground">
      <summary className="cursor-pointer select-none">What the agent did ({steps.length} steps)</summary>
      <ol className="mt-2 space-y-1 border-l pl-3">{steps.map((s, i) => <li key={i}>{s.text}</li>)}</ol>
    </details>}
    {working && <div className="pl-1"><Button variant="ghost" size="sm" onClick={onCancel} disabled={acting || purchase.status === 'paying'}>Stop</Button></div>}
  </section>;
}

function AccountOnly({ options, prominent = false }: { options: Purchase['options']; prominent?: boolean }) {
  const links = options.filter((o) => o.checkout === 'account_required');
  if (!links.length) return null;
  return <div className={prominent ? 'space-y-2' : 'space-y-2 rounded-lg bg-muted/60 p-3'}>
    {!prominent && <p className="text-xs text-muted-foreground">Cheaper, but the shop needs an account (buy these yourself):</p>}
    {links.map((o) => <a key={o.url} href={o.url} target="_blank" rel="noopener noreferrer"
      className="flex min-h-11 items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2 text-sm hover:bg-accent">
      <span className="min-w-0"><span className="block truncate font-medium">{o.title}</span>
        <span className="block truncate text-xs text-muted-foreground">{shortUrl(o.url)}</span></span>
      <span className="flex shrink-0 items-center gap-2 tabular-nums">{o.price_text}<ExternalLink className="size-3.5" /></span>
    </a>)}
    {prominent && <><Separator /><p className="text-xs text-muted-foreground">These shops need you to sign in, so the agent can’t check out for you.</p></>}
  </div>;
}

function tone(p: Purchase): 'good' | 'bad' | 'info' {
  if (p.status === 'ordered' || p.status === 'stopped_before_payment') return 'good';
  if (p.status === 'failed' || p.status === 'expired') return 'bad';
  return 'info';
}
