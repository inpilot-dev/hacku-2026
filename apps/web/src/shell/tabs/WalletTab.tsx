import { useEffect, useState } from 'react';
import { CreditCard, MapPin, Snowflake, Store, Wallet } from 'lucide-react';
import type { CardAuthorization } from '../../../../../contracts/types';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { api } from '@/lib/api';
import { categoryLabel, money, periodWord, shortDate } from '@/lib/format';
import { possessive } from '@/simple/holder';
import { cn } from '@/lib/utils';
import { PageHeader, Sticker } from '../components/chat';
import DeliveryDetails from '../components/DeliveryDetails';
import { ReceiptList } from '../components/Receipts';
import StoreAccounts from '../components/StoreAccounts';
import { storeName } from '../components/AllowanceSetup';
import { TOKEN, useAccount } from '../data/account';

/*
 * The wallet and the profile in one place: the grocery allowance and its virtual card, receipts, the delivery
 * details the one-time agent uses, and the store accounts.
 */

const MCC_NAMES: Record<string, string> = { '7995': 'Gambling', '6051': 'Quasi-cash', '4829': 'Money transfer', '5921': 'Liquor stores' };

export default function WalletTab() {
  const a = useAccount();
  const [revokeOpen, setRevokeOpen] = useState(false);

  return <div className="space-y-6">
    <PageHeader title="Wallet" who={a.mandate ? undefined : 'kip'}
      description="Your allowance, card, receipts and the details the agents use." />
    {a.error && <p className="rounded-lg bg-destructive/10 px-4 py-3 text-sm text-destructive" role="alert">{a.error}
      <button className="ml-2 underline" onClick={() => a.setError('')}>Dismiss</button></p>}

    {!a.loaded ? <Skeleton className="h-48 rounded-xl" /> : !a.mandate ? <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Wallet className="size-4" />No allowance yet</CardTitle>
        <CardDescription>Set a budget and rules, and the grocery agent can shop within them.</CardDescription></CardHeader>
      <CardContent><Button onClick={() => a.openSetup('new')}>Set up an allowance</Button></CardContent>
    </Card> : <AllowanceCard onRevoke={() => setRevokeOpen(true)} />}

    {a.mandate && <CardDetails />}

    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Sticker who="stella" size={32} />Receipts</CardTitle><CardDescription>Paid grocery orders, kept by Stella.</CardDescription>
        {a.receipts.records.length > 0 && <CardAction><Button variant="ghost" size="sm" onClick={() => void a.receipts.load(true)}>Refresh</Button></CardAction>}</CardHeader>
      <CardContent><ReceiptList /></CardContent>
    </Card>

    <Card id="delivery">
      <CardHeader><CardTitle className="flex items-center gap-2"><MapPin className="size-4" />Delivery details</CardTitle>
        <CardDescription>Used by the one-time agent for guest checkouts.</CardDescription></CardHeader>
      <CardContent><DeliveryDetails /></CardContent>
    </Card>

    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Store className="size-4" />Store accounts</CardTitle>
        <CardDescription>Sign in so the grocery agent can fill your real cart.</CardDescription></CardHeader>
      <CardContent><StoreAccounts online={a.online} allowedIds={a.mandate?.policy.allowed_merchant_ids} /></CardContent>
    </Card>

    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Sticker who="stella" size={32} />Activity</CardTitle><CardDescription>{a.fromServer ? 'From the wallet’s audit trail' : 'This session'}</CardDescription></CardHeader>
      <CardContent>{a.log.length ? <ol className="space-y-2 text-sm">{a.log.map((e) => <li key={e.id} className="flex gap-3">
        <time className="w-12 shrink-0 tabular-nums text-muted-foreground">{new Date(e.at).toLocaleTimeString('en-HK', { hour: '2-digit', minute: '2-digit', hour12: false })}</time>
        <span className={cn('mt-1.5 size-1.5 shrink-0 rounded-full', e.tone === 'good' ? 'bg-success' : e.tone === 'bad' ? 'bg-destructive' : 'bg-muted-foreground')} />
        <span>{e.text}</span></li>)}</ol> : <p className="text-sm text-muted-foreground">Nothing yet.</p>}</CardContent>
    </Card>

    <p className="text-center text-xs text-muted-foreground">
      <a className="underline underline-offset-2" href="?classic">Full dashboard</a> · <a className="underline underline-offset-2" href="?security">Security lab</a> · <a className="underline underline-offset-2" href="?about">About</a>
    </p>

    <Dialog open={revokeOpen} onOpenChange={setRevokeOpen}>
      <DialogContent>
        <DialogHeader><DialogTitle>Revoke this allowance?</DialogTitle>
          <DialogDescription>Its virtual card is cancelled for good and any holds are released. You can set up a new allowance afterwards.</DialogDescription></DialogHeader>
        <DialogFooter><DialogClose asChild><Button variant="outline">Keep it</Button></DialogClose>
          <Button variant="destructive" disabled={a.busy === 'revoke'} onClick={async () => { if (await a.revoke()) setRevokeOpen(false); }}>{a.busy === 'revoke' ? 'Revoking…' : 'Revoke'}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </div>;
}

function AllowanceCard({ onRevoke }: { onRevoke: () => void }) {
  const a = useAccount();
  const m = a.mandate!;
  const per = periodWord(m.policy.period_limits[0]?.period);
  const frozen = a.card?.status === 'frozen';
  const status = !a.active ? (m.status === 'expired' ? 'Expired' : 'Revoked') : frozen ? 'Paused' : 'Active';
  const locked = a.checkoutLocked;
  return <Card>
    <CardHeader>
      <CardDescription>{possessive(a.holder)} grocery allowance</CardDescription>
      <CardTitle className="text-3xl font-semibold tabular-nums">{a.active ? money(a.available) : money(0)}</CardTitle>
      <CardAction className="flex flex-col items-end gap-1">
        <Badge variant="outline" className={cn('border-0', status === 'Active' ? 'bg-brand-soft text-brand' : 'bg-destructive/10 text-destructive')}>{status}</Badge>
        <Sticker who="kip" state={status === 'Active' ? 'idle' : status === 'Paused' ? 'refused' : 'revoked'} size={64} />
      </CardAction>
    </CardHeader>
    <CardContent className="space-y-5">
      <div className="space-y-1.5">
        <Progress value={a.limit ? Math.min(100, (a.spent / a.limit) * 100) : 0} className="bg-brand-soft [&>[data-slot=progress-indicator]]:bg-brand" />
        <div className="flex justify-between text-xs text-muted-foreground"><span>{money(a.spent)} used</span><span>{money(a.limit)} a {per}</span></div>
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
        <Rule k="Most per order" v={money(m.policy.per_order_limit_minor)} />
        <Rule k={m.policy.allowed_merchant_ids.length > 1 ? 'Stores' : 'Store'} v={m.policy.allowed_merchant_ids.map(storeName).join(', ')} />
        <Rule k="Never buy" v={m.policy.blocked_categories.map(categoryLabel).join(', ') || 'Nothing blocked'} />
        {m.policy.approval_above_minor != null && <Rule k="Ask me above" v={money(m.policy.approval_above_minor)} />}
        {m.policy.risk_review && <Rule k="Unusual purchases" v="Reviewed" />}
        <Rule k="Ends" v={shortDate(m.policy.expires_at)} />
      </dl>
      <div className="flex flex-wrap gap-2">
        {a.active ? <>
          {frozen
            ? <Button onClick={() => void a.unfreeze()} disabled={a.busy === 'unfreeze' || locked}><Snowflake />{a.busy === 'unfreeze' ? 'Resuming…' : 'Unfreeze card'}</Button>
            : <Button variant="outline" onClick={() => void a.freeze()} disabled={!a.card || a.busy === 'freeze' || locked}><Snowflake />{a.busy === 'freeze' ? 'Freezing…' : 'Freeze card'}</Button>}
          <Button variant="outline" onClick={() => a.openSetup('change')} disabled={locked}>Change rules</Button>
          <Button variant="ghost" className="text-destructive hover:text-destructive" onClick={onRevoke} disabled={locked}>Revoke</Button>
        </> : <Button onClick={() => a.openSetup('new')}>Start a new allowance</Button>}
      </div>
      {locked && <p className="text-xs text-muted-foreground">A grocery checkout is still being resolved; card controls wait for it.</p>}
    </CardContent>
  </Card>;
}

function CardDetails() {
  const a = useAccount();
  const [auths, setAuths] = useState<CardAuthorization[] | null>(null);
  const card = a.card;
  useEffect(() => {
    if (!a.mandateId || !card) return;
    api.cardAuthorizations(TOKEN, a.mandateId).then((r) => setAuths(r.authorizations)).catch(() => setAuths([]));
  }, [a.mandateId, card]);
  if (!card) return null;
  const expiry = `${String(card.exp_month).padStart(2, '0')}/${String(card.exp_year).slice(-2)}`;
  const used = card.single_use_cards.used + card.single_use_cards.active + card.single_use_cards.cancelled;
  return <Card>
    <CardHeader><CardTitle className="flex items-center gap-2"><CreditCard className="size-4" />Virtual card</CardTitle>
      <CardDescription>The agent never sees this number. Each purchase gets its own single-use card, locked to the shop and amount.</CardDescription></CardHeader>
    <CardContent className="space-y-5">
      <div className={cn('flex aspect-[1.7] max-w-sm flex-col justify-between rounded-2xl bg-gradient-to-br from-neutral-900 to-neutral-700 p-5 text-white shadow-sm', card.status !== 'active' && 'opacity-60 grayscale')}
        role="img" aria-label={`${card.network === 'mastercard' ? 'Mastercard' : 'Visa'} ending ${card.last4}, expires ${expiry}, ${card.status}`}>
        <div className="flex items-center justify-between text-sm"><span className="font-semibold">Mandate</span>{card.status !== 'active' && <span className="rounded-full bg-white/15 px-2 py-0.5 text-xs capitalize">{card.status === 'frozen' ? 'Paused' : card.status}</span>}</div>
        <div className="font-mono text-lg tracking-widest">•••• •••• •••• {card.last4}</div>
        <div className="flex items-end justify-between text-xs"><span><span className="block text-white/60">Valid thru</span>{expiry}</span>
          <span className="text-base font-semibold italic">{card.network === 'mastercard' ? 'mastercard' : 'VISA'}</span></div>
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <Rule k="Most per purchase" v={money(card.controls.spend_limit_minor)} />
        <Rule k="Single-use cards" v={String(used)} />
        <Rule k="Always declined" v={card.controls.blocked_mccs.map((mcc) => MCC_NAMES[mcc] ?? `MCC ${mcc}`).join(', ')} />
        <Rule k="Issued" v={shortDate(card.issued_at)} />
      </dl>
      <div className="space-y-2">
        <p className="text-sm font-medium">Card activity</p>
        {auths === null ? <Skeleton className="h-10" /> : auths.length ? <ul className="space-y-1.5 text-sm">{auths.slice(0, 8).map((x) => <li key={x.id} className="flex justify-between gap-3">
          <span className="min-w-0 truncate">{storeName(x.merchant_id)} · ···· {x.card_last4}</span>
          <span className={cn('shrink-0 tabular-nums', !x.approved && 'text-destructive')}>{x.approved ? money(x.amount_minor) : 'Declined'}</span>
        </li>)}</ul> : <p className="text-sm text-muted-foreground">No card activity yet.</p>}
      </div>
    </CardContent>
  </Card>;
}

function Rule({ k, v }: { k: string; v: string }) {
  return <div className="min-w-0"><dt className="text-xs text-muted-foreground">{k}</dt><dd className="font-medium break-words">{v}</dd></div>;
}
