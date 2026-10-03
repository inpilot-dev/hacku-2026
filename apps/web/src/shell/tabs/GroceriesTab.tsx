import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Store, Carrot, Cherry, Mic, Minus, Pencil, Plus, Square, Wine, XCircle } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { categoryLabel, money, periodWord } from '@/lib/format';
import { cn } from '@/lib/utils';
import HistoryControls from '../components/HistoryControls';
import Basket from '../components/Basket';
import { Bubble, PageHeader, present, Sticker, Working } from '../components/chat';
import PresetEditor from '../components/PresetEditor';
import StoreAccounts from '../components/StoreAccounts';
import { useAccount } from '../data/account';
import { useGroceries, type Preset } from '../data/useGroceries';

/*
 * Repeat grocery shopping under the allowance: say or type the list, or pick a preset, and the agent builds a
 * basket at your allowed stores. The wallet checks your rules before anything is paid.
 */

export default function GroceriesTab({ onOpenWallet, active }: { onOpenWallet: () => void; active: boolean }) {
  const account = useAccount();
  const g = useGroceries();
  const [editing, setEditing] = useState<Preset | null>(null);
  const [shelfOpen, setShelfOpen] = useState(false);
  const [search, setSearch] = useState('');
  const end = useRef<HTMLDivElement>(null);
  const m = account.mandate;
  const [older, setOlder] = useState(0);
  const following = useRef(true);
  let currentStart = 0;
  for (let i = g.messages.length - 1; i >= 0; i--) {
    if (g.messages[i].startsConversation) { currentStart = i; break; }
  }
  const hidden = Math.max(0, Math.max(currentStart, g.messages.length - 6) - older);
  const currentId = g.messages[currentStart]?.id;
  useEffect(() => { setOlder(0); }, [currentId]);
  useEffect(() => {
    const track = () => { following.current = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 240; };
    window.addEventListener('scroll', track, { passive: true });
    return () => window.removeEventListener('scroll', track);
  }, []);

  useEffect(() => { if (active && !older && following.current && g.messages.length) end.current?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest' }); }, [g.messages.length, g.phase, active, older]);

  if (!account.loaded || (account.refreshError && !m)) return <div className="space-y-4"><Skeleton className="h-8 w-40" /><Skeleton className="h-32" /></div>;
  if (!m) return <div>
    <PageHeader title="Groceries" who="kumi" description="Repeat shopping under an allowance you set." />
    <Card><CardHeader><div className="mb-2"><Sticker who="bean" size={72} /></div><CardTitle>Set up an allowance first</CardTitle>
      <CardDescription>Choose a budget, a per-order limit, the stores and anything never to buy. The agent can only shop within it.</CardDescription></CardHeader>
      <CardContent><Button onClick={() => account.openSetup('new')}>Set up an allowance</Button></CardContent></Card>
  </div>;

  const per = periodWord(m.policy.period_limits[0]?.period);
  const blocked = new Set<string>(m.policy.blocked_categories);
  const filtered = g.products.filter((p) => p.title.toLowerCase().includes(search.trim().toLowerCase())).slice(0, 24);

  return <div className="flex flex-col">
    <PageHeader title="Groceries" who="kumi" state={g.verdict?.kind === 'paid' ? 'happy' : g.verdict?.kind === 'refused' ? 'sad' : 'idle'}
      description={<>{account.active ? `${money(account.available)} left this ${per}` : 'Allowance not active'} · {m.policy.allowed_merchant_ids.length} store{m.policy.allowed_merchant_ids.length > 1 ? 's' : ''}</>}
      action={<Button variant="ghost" size="sm" onClick={onOpenWallet}>Allowance</Button>} />

    {g.catalogError && <Alert className="mb-4"><AlertDescription className="flex items-center justify-between gap-3"><span>{g.catalogError}</span><Button variant="outline" size="sm" onClick={() => void g.loadCatalog()}>Retry</Button></AlertDescription></Alert>}
    {g.catalogLoading && <p className="mb-4 text-sm text-muted-foreground" role="status">Loading products…</p>}
    {!g.catalogLoading && !g.catalogError && !g.products.length && <p className="mb-4 text-sm text-muted-foreground">No products are available at this store.</p>}
    {(g.error || account.error) && <Alert variant="destructive" className="mb-4"><XCircle />
      <AlertDescription className="flex items-start justify-between gap-3"><span>{g.error || account.error}</span>
        <button className="underline" onClick={() => { g.setError(''); account.setError(''); }}>Dismiss</button></AlertDescription></Alert>}

    {!g.canSpend && <Alert className="mb-4 grid-cols-[auto_1fr] gap-x-3">
      <Sticker who="kip" state={!account.active ? 'revoked' : 'refused'} size={44} className="row-span-2" />
      <AlertTitle>{g.recoveryBlocked || g.busy === 'checkout-restore' ? 'Checking your previous checkout' : !account.active ? 'The allowance is not active' : 'The card is frozen'}</AlertTitle>
      <AlertDescription>
        <p>{g.recoveryBlocked || g.busy === 'checkout-restore' ? 'Your previous payment must be resolved before a new purchase. No new payment has been submitted.'
          : !account.active ? `This allowance is ${m.status === 'expired' ? 'expired' : 'revoked'}. Nobody can spend from it.` : 'Unfreeze it to shop again.'}</p>
        {account.active && !g.recoveryBlocked && g.busy !== 'checkout-restore' && <Button size="sm" className="mt-2" disabled={account.busy === 'unfreeze'} onClick={() => void account.unfreeze()}>Unfreeze the card</Button>}
        {!account.active && <Button size="sm" className="mt-2" onClick={() => account.openSetup('new')}>Start a new allowance</Button>}
      </AlertDescription>
    </Alert>}

    <HistoryControls hidden={hidden} expanded={older > 0} onMore={() => setOlder((n) => n + 8)} onLatest={() => setOlder(0)} />
    <div className="space-y-3">
      {g.messages.length === 0 && g.canSpend && <Bubble from="kumi">{`What’s on your list${account.holder ? ` for ${account.holder}` : ''}?`} Pick a preset or tell me what you need.</Bubble>}
      {g.messages.slice(hidden).map((msg) => <Bubble key={msg.id} from={msg.who} tone={msg.tone}>{msg.who === 'you' ? msg.text : present(msg.text)}</Bubble>)}
      {(g.phase === 'packing' || g.busy === 'parse') && <Working who="kumi">{g.busy === 'parse' ? 'Reading your list…' : `Packing${g.preset ? ` ${g.preset.title.toLowerCase()}` : ''} at your stores…`}</Working>}
      {g.quote && (g.phase === 'basket' || g.phase === 'paying' || g.phase === 'verdict') && <Basket g={g} />}
      <div ref={end} />
    </div>

    {g.canSpend && g.phase === 'pick' && <div className="mt-6 space-y-6">
      <section className="space-y-2" aria-label="Presets">
        <p className="text-sm text-muted-foreground">Presets</p>
        <div className="grid gap-2 sm:grid-cols-2">
          {g.presets.map((p) => <div key={p.id} className="group relative">
            <button onClick={() => void g.shop(p)} disabled={!g.products.length}
              className="flex min-h-16 w-full items-center gap-3 rounded-2xl border bg-card px-3 py-3 pr-12 text-left transition-colors hover:bg-accent disabled:opacity-50">
              <PresetTile id={p.id} />
              <span className="min-w-0">
                <span className="flex items-center gap-2 text-sm font-medium">{p.title}{p.ruleTest && <Badge variant="outline" className="font-normal">Rule test</Badge>}</span>
                <span className="line-clamp-1 text-xs text-muted-foreground">{p.subtitle}</span>
                <span className="text-xs text-muted-foreground tabular-nums">{p.edited ? 'Your list' : g.products.length ? `~${money(g.estimate(p))}` : ''}</span>
              </span>
            </button>
            <Button variant="ghost" size="icon" className="absolute top-2 right-2" aria-label={`Edit ${p.title}`} onClick={() => setEditing(p)}><Pencil /></Button>
          </div>)}
        </div>
      </section>

      <section className="space-y-2">
        <Button variant="link" className="h-auto p-0 text-muted-foreground" onClick={() => setShelfOpen((v) => !v)}>{shelfOpen ? 'Hide the shelf' : 'Or pick items yourself'}</Button>
        {shelfOpen && <div className="space-y-3 rounded-xl border p-3">
          <Input placeholder="Search products" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search products" />
          <ul className="divide-y">{!filtered.length && <li className="py-4 text-center text-sm text-muted-foreground">No matching items.</li>}{filtered.map((p) => { const q = g.custom[p.id] ?? 0; return <li key={p.id} className="flex min-h-12 items-center gap-3 py-1.5 text-sm">
            <span className="min-w-0 flex-1">{p.title}{blocked.has(p.category) && <Badge variant="outline" className="ml-2 text-destructive">{categoryLabel(p.category)}</Badge>}</span>
            <span className="tabular-nums text-muted-foreground">{money(p.unit_price_minor)}</span>
            <span className="flex items-center gap-1">
              {q > 0 && <><Button variant="outline" size="icon-sm" aria-label={`Remove one ${p.title}`} onClick={() => g.setCustom((c) => ({ ...c, [p.id]: Math.max(0, q - 1) }))}><Minus /></Button>
                <span className="w-5 text-center tabular-nums">{q}</span></>}
              <Button variant="outline" size="icon-sm" aria-label={`Add one ${p.title}`} onClick={() => g.setCustom((c) => ({ ...c, [p.id]: Math.min(20, q + 1) }))}><Plus /></Button>
            </span>
          </li>; })}</ul>
          <Button className="w-full" disabled={!g.customCount} onClick={() => void g.shop('custom')}>
            {g.customCount ? `Price ${g.customCount} item${g.customCount > 1 ? 's' : ''}, about ${money(g.customTotal)}` : 'Add something first'}</Button>
        </div>}
      </section>
    </div>}

    {/* The list box only while picking: once a basket is up, its own buttons are the next step. */}
    {g.canSpend && g.phase === 'pick' && <div className="fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] shell-composer z-10 bg-gradient-to-t from-background via-background to-transparent pt-4 md:bottom-0"><div className="mx-auto max-w-3xl px-4 pb-3">
      <form className="relative rounded-2xl border bg-card shadow-sm focus-within:ring-2 focus-within:ring-ring/40" onSubmit={(e) => { e.preventDefault(); void g.shopFromText(); }}>
        <Textarea value={g.listText} onChange={(e) => g.setListText(e.target.value)} rows={2}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !g.voice) { e.preventDefault(); void g.shopFromText(); } }}
          placeholder="Type or say the list: rice, two litres of milk, 3 apples"
          aria-label="Shopping list" disabled={g.phase !== 'pick' || g.busy === 'parse' || g.voice === 'transcribing'}
          className="min-h-14 resize-none border-0 bg-transparent pr-28 shadow-none focus-visible:ring-0" />
        <div className="absolute right-2 bottom-2 flex gap-1.5">
          <Button type="button" variant={g.voice === 'recording' ? 'destructive' : 'outline'} size="icon" className={cn('size-10 rounded-full', g.voice === 'recording' && 'animate-pulse')}
            onClick={() => void g.toggleRecording()} disabled={g.voice === 'transcribing' || g.busy === 'parse'}
            aria-label={g.voice === 'recording' ? 'Stop recording' : 'Speak the list'}>{g.voice === 'recording' ? <Square /> : <Mic />}</Button>
          <Button type="submit" size="icon" className="size-10 rounded-full" aria-label="Send list"
            disabled={g.phase !== 'pick' || !g.listText.trim() || g.busy === 'parse' || Boolean(g.voice) || !g.products.length}><ArrowUp /></Button>
        </div>
      </form>
      {g.voice === 'transcribing' && <p className="mt-1 text-xs text-muted-foreground">Transcribing…</p>}
    </div></div>}

    <Card className="mt-8">
      <CardHeader><CardTitle className="flex items-center gap-2"><Store className="size-4" />Your stores</CardTitle>
        <CardDescription>Sign in so Kumi can fill your real cart. It never checks out.</CardDescription></CardHeader>
      <CardContent><StoreAccounts online={account.online} allowedIds={m.policy.allowed_merchant_ids} /></CardContent>
    </Card>

    <p className="mt-6 text-xs text-muted-foreground">Prices from a Wellcome snapshot{g.snapshotAt ? ` taken ${g.snapshotAt}` : ''}. Click &amp; Collect, free above HK$50. Payments run in a sandbox.</p>
    <PresetEditor g={g} preset={editing} onClose={() => setEditing(null)} />
  </div>;
}

// The previous app's preset tiles: green for everyday, orange for fruit, pink for the rule test.
const TILES: Record<string, { icon: typeof Carrot; className: string }> = {
  basics: { icon: Carrot, className: 'bg-[#d9f4e4] text-[#00692a]' },
  fruit: { icon: Cherry, className: 'bg-[#ffe7d9] text-[#b9480d]' },
  champagne: { icon: Wine, className: 'bg-[#ffe8f3] text-[#ba437a]' },
};

function PresetTile({ id }: { id: string }) {
  const tile = TILES[id] ?? TILES.basics;
  const Icon = tile.icon;
  return <span className={cn('grid size-11 shrink-0 place-items-center rounded-xl', tile.className)}><Icon className="size-5" strokeWidth={2.2} /></span>;
}
