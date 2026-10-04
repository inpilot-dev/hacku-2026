import { useLocale } from '@/lib/locale';
import CommerceTools from '../components/CommerceTools';
import { useEffect, useState } from 'react';
import { Snowflake, Wallet } from 'lucide-react';
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
import { ReceiptList } from '../components/Receipts';
import { storeName } from '../components/AllowanceSetup';
import { TOKEN, useAccount } from '../data/account';

/*
 * The wallet: the allowance's virtual card with the money left on it and its controls, the rules it carries,
 * receipts and activity. Store accounts live in Groceries; delivery details in Profile.
 */

const MCC_NAMES: Record<string, string> = { '7995': 'Gambling', '6051': 'Quasi-cash', '4829': 'Money transfer', '5921': 'Liquor stores' };

export default function WalletTab() {
  const a = useAccount();
  const { t } = useLocale();
  const [revokeOpen, setRevokeOpen] = useState(false);

  return <div className="space-y-6">
    <PageHeader title={t("Wallet", "錢包")} description="Your card, what’s left on it, and every purchase." />
    {a.error && <p className="rounded-lg bg-destructive/10 px-4 py-3 text-sm text-destructive" role="alert">{a.error}
      <button className="ml-2 underline" onClick={() => a.setError('')}>Dismiss</button></p>}

    {(!a.loaded || (a.refreshError && !a.mandate)) ? <Skeleton className="h-64 rounded-2xl" /> : !a.mandate ? <Card>
      <CardHeader><div className="mb-2"><Sticker who="kip" size={72} /></div>
        <CardTitle className="flex items-center gap-2"><Wallet className="size-4" />No card yet</CardTitle>
        <CardDescription>Set a budget and rules, and Kip issues a virtual card the grocery agent can only spend within them.</CardDescription></CardHeader>
      <CardContent><Button onClick={() => a.openSetup('new')}>Set up an allowance</Button></CardContent>
    </Card> : <WalletCard onRevoke={() => setRevokeOpen(true)} />}

    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Sticker who="stella" size={32} />{t("Receipts", "收據")}</CardTitle><CardDescription>Paid grocery orders, kept by Stella.</CardDescription>
        {a.receipts.records.length > 0 && <CardAction><Button variant="ghost" size="sm" onClick={() => void a.receipts.load(true)}>Refresh</Button></CardAction>}</CardHeader>
      <CardContent><ReceiptList /></CardContent>
    </Card>

    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Sticker who="stella" size={32} />{t("Activity", "活動")}</CardTitle><CardDescription>{a.fromServer ? 'From the wallet’s audit trail' : 'This session'}</CardDescription></CardHeader>
      <CardContent>{a.log.length ? <ol className="space-y-2 text-sm">{a.log.map((e) => <li key={e.id} className="flex gap-3">
        <time className="w-12 shrink-0 tabular-nums text-muted-foreground">{new Date(e.at).toLocaleTimeString('en-HK', { hour: '2-digit', minute: '2-digit', hour12: false })}</time>
        <span className={cn('mt-1.5 size-1.5 shrink-0 rounded-full', e.tone === 'good' ? 'bg-success' : e.tone === 'bad' ? 'bg-destructive' : 'bg-muted-foreground')} />
        <span>{e.text}</span></li>)}</ol> : <p className="text-sm text-muted-foreground">Nothing yet.</p>}</CardContent>
    </Card>

    <CommerceTools />
    <p className="text-center text-xs text-muted-foreground">
      <a className="underline underline-offset-2" href="?classic">Full dashboard</a> · <a className="underline underline-offset-2" href="?security">Security lab</a> · <a className="underline underline-offset-2" href="?about">About</a>
    </p>

    <Dialog open={revokeOpen} onOpenChange={setRevokeOpen}>
      <DialogContent>
        <DialogHeader><DialogTitle>Revoke this allowance?</DialogTitle>
          <DialogDescription>{t('Its virtual card is cancelled. Local sandbox holds are released; external TEST payment holds stay until the provider confirms cancellation or recovery. You can set up a new allowance afterwards.', '虛擬卡會取消。本機沙盒預留會釋放；外部測試付款額度會保留至付款服務確認取消或復原。之後可設定新授權。')}</DialogDescription></DialogHeader>
        <DialogFooter><DialogClose asChild><Button variant="outline">Keep it</Button></DialogClose>
          <Button variant="destructive" disabled={a.busy === 'revoke'} onClick={async () => { if (await a.revoke()) setRevokeOpen(false); }}>{a.busy === 'revoke' ? 'Revoking…' : 'Revoke'}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </div>;
}

/** The virtual card with the money left on it, its controls and the rules it carries. */
function WalletCard({ onRevoke }: { onRevoke: () => void }) {
  const a = useAccount();
  const m = a.mandate!;
  const card = a.card;
  const per = periodWord(m.policy.period_limits[0]?.period);
  const frozen = card?.status === 'frozen';
  const status = !a.active ? (m.status === 'expired' ? 'Expired' : 'Revoked') : frozen ? 'Frozen' : 'Active';
  const locked = a.checkoutLocked;
  const expiry = card ? `${String(card.exp_month).padStart(2, '0')}/${String(card.exp_year).slice(-2)}` : '';
  const [auths, setAuths] = useState<CardAuthorization[] | null>(null);

  useEffect(() => {
    if (!a.mandateId || !card) return;
    api.cardAuthorizations(TOKEN, a.mandateId).then((r) => setAuths(r.authorizations)).catch(() => setAuths([]));
  }, [a.mandateId, card]);

  return <Card>
    <CardContent className="space-y-5">
      {/* The card itself: what's left this period, on the card the agent pays with. */}
      <div className={cn('wallet-hero relative flex min-h-60 w-full flex-col justify-between overflow-hidden rounded-3xl p-6 text-white',
        status !== 'Active' && 'wallet-hero-inactive')}
        role="img" aria-label={`${possessive(a.holder)} grocery card, ${a.active ? money(a.available) : money(0)} left this ${per}, ${status}${card ? `, ending ${card.last4}` : ''}`}>
        <div className="flex items-start justify-between">
          <div>
            <p className="text-sm font-semibold">Mandate</p>
            <p className="text-xs text-white/60">{possessive(a.holder)} groceries</p>
          </div>
          <Badge className={cn('border-0', status === 'Active' ? 'bg-[#00a240] text-white' : 'bg-white/20 text-white')}>{frozen && <Snowflake />}{status}</Badge>
        </div>
        <div>
          <p className="text-xs text-white/60">{a.active ? `Left this ${per}` : 'Spending is off'}</p>
          <p className="wallet-balance font-semibold tracking-tight tabular-nums">{a.active ? money(a.available) : money(0)}</p>
        </div>
        <div className="flex items-end justify-between text-xs">
          <span className="font-mono text-sm tracking-widest">{card ? `•••• ${card.last4}` : 'No card number'}</span>
          {card && <span className="text-right"><span className="block text-white/60">Valid thru</span>{expiry}</span>}
          {card && <span className="text-base font-semibold italic">{card.network === 'mastercard' ? 'mastercard' : 'VISA'}</span>}
        </div>
        <span className="wallet-mascot"><Sticker who="kip" state={status === 'Active' ? 'idle' : frozen ? 'refused' : 'revoked'} size={56} /></span>
      </div>

      <div className="space-y-1.5">
        <Progress value={a.limit ? Math.min(100, (a.spent / a.limit) * 100) : 0} className="bg-brand-soft [&>[data-slot=progress-indicator]]:bg-brand" />
        <div className="flex justify-between text-xs text-muted-foreground"><span>{money(a.spent)} used</span><span>{money(a.limit)} a {per}</span></div>
      </div>

      <div className="flex flex-wrap gap-2">
        {a.active ? <>
          {frozen
            ? <Button onClick={() => void a.unfreeze()} disabled={a.busy === 'unfreeze' || locked}><Snowflake />{a.busy === 'unfreeze' ? 'Unfreezing…' : 'Unfreeze card'}</Button>
            : <Button variant="outline" onClick={() => void a.freeze()} disabled={!card || a.busy === 'freeze' || locked}><Snowflake />{a.busy === 'freeze' ? 'Freezing…' : 'Freeze card'}</Button>}
          <Button variant="outline" onClick={() => a.openSetup('change')} disabled={locked}>Change rules</Button>
          <Button variant="ghost" className="text-destructive hover:text-destructive" onClick={onRevoke} disabled={locked}>Revoke</Button>
        </> : <Button onClick={() => a.openSetup('new')}>Start a new allowance</Button>}
      </div>
      {locked && <p className="text-xs text-muted-foreground">A grocery checkout is still being resolved; card controls wait for it.</p>}

      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 border-t pt-4 text-sm sm:grid-cols-3">
        <Rule k="Most per order" v={money(m.policy.per_order_limit_minor)} />
        <Rule k={m.policy.allowed_merchant_ids.length > 1 ? 'Stores' : 'Store'} v={m.policy.allowed_merchant_ids.map(storeName).join(', ')} />
        <Rule k="Never buy" v={m.policy.blocked_categories.map(categoryLabel).join(', ') || 'Nothing blocked'} />
        {m.policy.approval_above_minor != null && <Rule k="Ask me above" v={money(m.policy.approval_above_minor)} />}
        {m.policy.risk_review && <Rule k="Unusual purchases" v="Reviewed" />}
        {m.policy.web_purchases && <Rule k="Web purchases" v="Allowed, you approve each" />}
        <Rule k="Ends" v={shortDate(m.policy.expires_at)} />
        {card && <Rule k="Always declined" v={card.controls.blocked_mccs.map((mcc) => MCC_NAMES[mcc] ?? `MCC ${mcc}`).join(', ')} />}
        {card && <Rule k="Single-use cards" v={String(card.single_use_cards.used + card.single_use_cards.active + card.single_use_cards.cancelled)} />}
      </dl>
      <p className="text-xs text-muted-foreground">The agent never sees this card number. Each purchase gets its own single-use card, locked to the shop and the amount.</p>

      {card && <div className="space-y-2 border-t pt-4">
        <p className="text-sm font-medium">Card activity</p>
        {auths === null ? <Skeleton className="h-10" /> : auths.length ? <ul className="space-y-1.5 text-sm">{auths.slice(0, 8).map((x) => <li key={x.id} className="flex justify-between gap-3">
          <span className="min-w-0 truncate">{storeName(x.merchant_id)} · ···· {x.card_last4}</span>
          <span className={cn('shrink-0 tabular-nums', !x.approved && 'text-destructive')}>{x.approved ? money(x.amount_minor) : 'Declined'}</span>
        </li>)}</ul> : <p className="text-sm text-muted-foreground">No card activity yet.</p>}
      </div>}
    </CardContent>
  </Card>;
}

function Rule({ k, v }: { k: string; v: string }) {
  return <div className="min-w-0"><dt className="text-xs text-muted-foreground">{k}</dt><dd className="font-medium break-words">{v}</dd></div>;
}
