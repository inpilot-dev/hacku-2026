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
import { cn } from '@/lib/utils';
import { Bubble, Linkified, PageHeader, shortUrl, Working } from '../components/chat';
import HistoryControls from '../components/HistoryControls';
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

export default function BuyTab({ onOpenProfile, active, profileRevision }: { onOpenProfile: () => void; active: boolean; profileRevision: number }) {
  const p = usePurchases();
  const [text, setText] = useState('');
  useEffect(() => { if (profileRevision > 0) p.setNeedsProfile(false); }, [profileRevision, p.setNeedsProfile]);
  const end = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const [older, setOlder] = useState(0);
  const hidden = Math.max(0, p.purchases.length - 1 - older);
  const latest = p.purchases[p.purchases.length - 1];
  const progress = latest ? `${latest.id}:${latest.status}:${latest.events.length}` : '';
  useEffect(() => {
    const track = () => { following.current = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 240; };
    window.addEventListener('scroll', track, { passive: true });
    return () => window.removeEventListener('scroll', track);
  }, []);

  useEffect(() => { if (active && !older && following.current) end.current?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest' }); }, [progress, active, older]);

  async function send(value = text) {
    if (await p.start(value)) { setText(''); setOlder(0); }
  }

  return <div className="flex flex-col">
    <PageHeader title="Buy anything" who="kumi" state={p.purchases.some((x) => x.status === 'ordered' || x.status === 'stopped_before_payment') ? 'happy' : 'idle'}
      description="Find a match. Review the total before paying."
      action={p.purchases.length > 0 && !p.busy ? <Button variant="ghost" size="sm" onClick={() => { p.clear(); setOlder(0); }}>New conversation</Button> : undefined} />

    {p.purchases.length === 0 && <div className="mb-6 space-y-4">
      <Bubble from="kumi">What are you looking for? Tell me your budget and any must-haves.</Bubble>
      <div className="grid gap-2 pl-[46px]">
        {SUGGESTIONS.map((s) => <button key={s} onClick={() => setText(s)}
          className="min-h-11 rounded-2xl border bg-card px-4 py-3 text-left text-sm transition-colors hover:bg-accent">{s}</button>)}
      </div>
    </div>}

    <HistoryControls hidden={hidden} expanded={older > 0} onMore={() => setOlder((count) => count + 3)} onLatest={() => setOlder(0)} />
    {p.refreshError && <Alert className="mb-4"><AlertDescription className="flex items-center justify-between gap-3"><span>{p.refreshError}</span><Button variant="outline" size="sm" onClick={p.retry}>Retry</Button></AlertDescription></Alert>}
    <div className="space-y-6">
      {p.purchases.slice(hidden).map((purchase) => <Thread key={purchase.id} purchase={purchase} acting={p.acting === purchase.id || p.uncertain.includes(purchase.id)} checking={p.uncertain.includes(purchase.id)}
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

    {/* The composer sits above the phone tab bar; while a purchase is open it stays in the flow so it never covers
        the approval buttons. */}
    <div className="fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] shell-composer z-10 bg-gradient-to-t from-background via-background to-transparent pt-4 md:bottom-0"><div className="mx-auto max-w-3xl px-4 pb-3">
      <form className="relative rounded-2xl border bg-card shadow-sm focus-within:ring-2 focus-within:ring-ring/40"
        onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={1000}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }}
          placeholder={p.restoring ? 'Checking saved purchases…' : p.busy ? 'Finish or cancel the current purchase first' : 'What do you want to buy?'}
          aria-label="What do you want to buy?" disabled={p.sending || p.busy}
          className="min-h-14 resize-none border-0 bg-transparent pr-14 shadow-none focus-visible:ring-0" />
        <Button type="submit" size="icon" className="absolute right-2 bottom-2 size-10 rounded-full"
          disabled={!text.trim() || p.sending || p.busy} aria-label="Send">{p.sending ? <span className="text-xs">…</span> : <ArrowUp />}</Button>
      </form>
    </div></div>
  </div>;
}

function Thread({ purchase, acting, checking, onApprove, onCancel }: { purchase: Purchase; acting: boolean; checking: boolean; onApprove: () => void; onCancel: () => void }) {
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
      <Working who={purchase.status === 'paying' ? 'kip' : 'kumi'}>{STATUS_TEXT[purchase.status] ?? 'Working…'}</Working>
      {steps.length > 0 && <p className="pl-[46px] text-xs text-muted-foreground line-clamp-2">{steps[steps.length - 1].text}</p>}
    </div>}

    {!working && purchase.status !== 'awaiting_approval' && <Bubble from={speaker(purchase)} tone={tone(purchase)}><Linkified text={presentPurchase(purchase.message)} /></Bubble>}
    {purchase.status === 'awaiting_approval' && <Bubble from="kip">Kumi got to the shop’s card form. Here’s exactly what it will cost; I only pay once you approve this total.</Bubble>}

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
          {checking ? 'Checking status…' : acting ? 'Submitting…' : `Approve and pay ${purchase.order.total_text ?? money(purchase.order.total_minor)}`}</Button>
        <Button variant="outline" className="min-h-11" onClick={onCancel} disabled={acting}>Cancel</Button>
      </CardFooter>
      {!purchase.live_payments && <p className="px-6 text-xs text-muted-foreground">Sandbox: the card is filled in but the order is not placed.</p>}
    </Card>}

    {purchase.status === 'needs_account' && <AccountOnly options={purchase.options} prominent />}

    {steps.length > 0 && !working && <details className="pl-1 text-xs text-muted-foreground">
      <summary className="inline-flex min-h-10 cursor-pointer items-center select-none">Activity ({steps.length})</summary>
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

/** Kip speaks for paying (the wallet); Kumi for finding and checking out. */
function speaker(p: Purchase): 'kumi' | 'kip' {
  return ['ordered', 'stopped_before_payment', 'needs_user', 'paying'].includes(p.status) ? 'kip' : 'kumi';
}

function tone(p: Purchase): 'good' | 'bad' | 'info' {
  if (p.status === 'ordered' || p.status === 'stopped_before_payment') return 'good';
  if (p.status === 'failed' || p.status === 'expired') return 'bad';
  return 'info';
}

function presentPurchase(text: string) {
  if (text.startsWith('Steel browser is not reachable')) return 'I couldn’t connect to the shopping browser. Please try again shortly.';
  if (text.includes('OPENROUTER_KEY is not set') || text.startsWith('Missing TYPESAFE')) return 'The shopping service isn’t configured yet. Please try again later.';
  return text;
}
