import { useEffect, useState } from 'react';
import { Check } from 'lucide-react';
import type { Category, PeriodLimit, Policy } from '../../../../../contracts/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { api, ApiError } from '@/lib/api';
import { categoryLabel, money, periodWord } from '@/lib/format';
import { cn } from '@/lib/utils';
import { TOKEN, useAccount } from '../data/account';
import StoreAccounts from './StoreAccounts';
import { Sticker } from './chat';

/*
 * Set up (or change) the grocery allowance: who it's for and the limits, the stores, then the exact permission to
 * confirm. Moved from simple/Onboarding.tsx; the wallet stays the only authority, this only submits a policy for
 * the user to confirm.
 */

const DRAFT_ID = 'draft_demo';
const DELEGATEE_ID = 'agent_student';
const BLOCKABLE: Category[] = ['alcohol', 'beverage_non_alcoholic', 'pantry', 'produce', 'dairy', 'eggs', 'meat', 'seafood', 'bakery', 'household'];
const KNOWN_STORES: Record<string, string> = { wellcome: 'Wellcome', marketplace: 'Market Place' };
export const storeName = (id: string) => KNOWN_STORES[id] ?? id;

function fourWeeksIso() {
  const end = new Date(Date.now() + 28 * 864e5);
  return `${end.toISOString().slice(0, 10)}T23:59:59+08:00`;
}
const dollars = (minor: number) => String(Math.round(minor / 100));

type Step = 'rules' | 'stores' | 'review';

export default function AllowanceSetup({ open, onOpenChange, change }: { open: boolean; onOpenChange: (open: boolean) => void; change: boolean }) {
  const account = useAccount();
  const start = change ? account.mandate?.policy ?? null : null;
  const [step, setStep] = useState<Step>('rules');
  const [holder, setHolder] = useState('');
  const [period, setPeriod] = useState<PeriodLimit['period']>('calendar_week');
  const [budget, setBudget] = useState('800');
  const [perOrder, setPerOrder] = useState('300');
  const [askOn, setAskOn] = useState(false);
  const [askAbove, setAskAbove] = useState('100');
  const [riskReview, setRiskReview] = useState(false);
  const [blocked, setBlocked] = useState<Category[]>(['alcohol']);
  const [allowed, setAllowed] = useState<Record<string, boolean>>({ wellcome: true, marketplace: true });
  const [review, setReview] = useState<Policy | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // Each opening starts from the current allowance (when changing it) or the defaults.
  useEffect(() => {
    if (!open) return;
    setStep('rules'); setError(''); setReview(null);
    setHolder(change ? account.holder : '');
    setPeriod(start?.period_limits[0]?.period ?? 'calendar_week');
    setBudget(start?.period_limits[0] ? dollars(start.period_limits[0].limit_minor) : '800');
    setPerOrder(start ? dollars(start.per_order_limit_minor) : '300');
    setAskOn(start?.approval_above_minor != null);
    setAskAbove(start?.approval_above_minor != null ? dollars(start.approval_above_minor) : '100');
    setRiskReview(start?.risk_review ?? false);
    setBlocked(start?.blocked_categories ?? ['alcohol']);
    setAllowed(start ? Object.fromEntries(start.allowed_merchant_ids.map((id) => [id, true])) : { wellcome: true, marketplace: true });
  }, [open]);

  function rulesValid() {
    const budgetMinor = Math.round(Number(budget) * 100);
    const orderMinor = Math.round(Number(perOrder) * 100);
    const askMinor = askOn ? Math.round(Number(askAbove) * 100) : null;
    if (!(budgetMinor > 0) || !(orderMinor > 0) || (askMinor !== null && !(askMinor > 0))) { setError('Use whole HK$ amounts above zero.'); return null; }
    if (orderMinor > budgetMinor) { setError(`The most per order can’t be more than the ${periodWord(period)}ly budget.`); return null; }
    return { budgetMinor, orderMinor, askMinor };
  }

  /** A draft backs exactly one mandate, so every switch-on registers its own; falls back to the seeded draft. */
  async function freshDraftId(text: string): Promise<string> {
    try {
      const draft = await api.draft(TOKEN, { text, delegatee_id: DELEGATEE_ID });
      sessionStorage.removeItem('mandate-idempotency-mandate-draft');
      return draft.draft_id;
    } catch (err) {
      if (err instanceof ApiError && [404, 405].includes(err.status)) return DRAFT_ID;
      throw err;
    }
  }

  function toReview() {
    setError('');
    const rules = rulesValid();
    if (!rules) { setStep('rules'); return; }
    const merchants = Object.entries(allowed).filter(([, on]) => on).map(([id]) => id);
    if (!merchants.length) { setError('Pick at least one store the agent may shop at.'); return; }
    setReview({
      currency: 'HKD',
      per_order_limit_minor: rules.orderMinor,
      period_limits: [{ period, limit_minor: rules.budgetMinor, timezone: 'Asia/Hong_Kong' }],
      allowed_merchant_ids: merchants,
      blocked_categories: blocked,
      expires_at: fourWeeksIso(),
      approval_above_minor: rules.askMinor,
      risk_review: riskReview,
    });
    setStep('review');
  }

  async function activate() {
    if (!review || busy) return;
    const limit = review.period_limits[0];
    const names = review.allowed_merchant_ids.map(storeName).join(' and ');
    const per = periodWord(limit.period);
    setError(''); setBusy(true);
    try {
      const never = review.blocked_categories.length ? `no ${review.blocked_categories.map((c) => categoryLabel(c).toLowerCase()).join(', ')}` : 'nothing blocked';
      const draftId = await freshDraftId(`Allowance for ${holder.trim() || 'me'}: ${money(review.per_order_limit_minor)} per order and ${money(limit.limit_minor)} per ${per}, only from ${names}, ${never}.`);
      const result = await api.confirm(TOKEN, { draft_id: draftId, policy: review });
      sessionStorage.removeItem('mandate-idempotency-confirm-draft-demo');
      await account.activated(result, `Allowance on: ${money(limit.limit_minor)}/${per}, ${money(review.per_order_limit_minor)}/order at ${names}`, holder.trim());
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The wallet could not switch this on.');
    } finally { setBusy(false); }
  }

  const steps: { id: Step; label: string }[] = [{ id: 'rules', label: 'Rules' }, { id: 'stores', label: 'Stores' }, { id: 'review', label: 'Confirm' }];
  const at = steps.findIndex((s) => s.id === step);

  return <Dialog open={open} onOpenChange={(next) => { if (!busy) onOpenChange(next); }}>
    <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-lg">
      <DialogHeader className="flex-row items-center gap-3 text-left">
        <Sticker who="bean" state={step === 'review' ? 'done' : 'idle'} size={56} className="shrink-0" />
        <div className="space-y-1.5">
        <DialogTitle>{change ? 'Change the allowance' : 'Set up an allowance'}</DialogTitle>
        <DialogDescription>Bean helps you set the rules. Kumi can only shop within them, and nothing is active until you confirm.</DialogDescription>
        </div>
      </DialogHeader>

      <ol className="flex gap-2 text-xs" aria-label="Setup steps">
        {steps.map((s, i) => <li key={s.id} className={cn('flex items-center gap-1.5 rounded-full px-2.5 py-1', i === at ? 'bg-primary text-primary-foreground' : i < at ? 'bg-muted' : 'text-muted-foreground')}>
          {i < at ? <Check className="size-3" /> : <span>{i + 1}</span>}{s.label}</li>)}
      </ol>

      {error && <p className="text-sm text-destructive" role="alert">{error}</p>}

      {step === 'rules' && <div className="grid gap-4">
        <Field label="Who it’s for" htmlFor="holder"><Input id="holder" value={holder} maxLength={40} placeholder="Me" onChange={(e) => setHolder(e.target.value)} /></Field>
        <Field label="Budget resets">
          <ToggleGroup type="single" variant="outline" value={period} onValueChange={(v) => v && setPeriod(v as PeriodLimit['period'])}>
            <ToggleGroupItem value="calendar_week" className="px-4">Weekly</ToggleGroupItem>
            <ToggleGroupItem value="calendar_month" className="px-4">Monthly</ToggleGroupItem>
          </ToggleGroup>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={`${period === 'calendar_month' ? 'Monthly' : 'Weekly'} budget (HK$)`} htmlFor="budget"><Input id="budget" inputMode="numeric" value={budget} onChange={(e) => setBudget(e.target.value)} /></Field>
          <Field label="Most per order (HK$)" htmlFor="per-order"><Input id="per-order" inputMode="numeric" value={perOrder} onChange={(e) => setPerOrder(e.target.value)} /></Field>
        </div>
        <SwitchRow id="ask" label="Ask me before bigger orders" checked={askOn} onChange={setAskOn} />
        {askOn && <Field label="Ask above (HK$)" htmlFor="ask-above"><Input id="ask-above" inputMode="numeric" value={askAbove} onChange={(e) => setAskAbove(e.target.value)} /></Field>}
        <SwitchRow id="risk" label="Review unusual purchases" hint="Pause first-time or unusually large baskets, new items and sharp price rises." checked={riskReview} onChange={setRiskReview} />
        <Field label="Never buy">
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Categories always refused">
            {BLOCKABLE.map((c) => { const on = blocked.includes(c); return <button key={c} type="button" aria-pressed={on}
              onClick={() => setBlocked((cur) => on ? cur.filter((x) => x !== c) : [...cur, c])}
              className={cn('min-h-9 rounded-full border px-3 text-sm transition-colors', on ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-accent')}>{categoryLabel(c)}</button>; })}
          </div>
        </Field>
        <p className="text-xs text-muted-foreground">Lasts 4 weeks.</p>
      </div>}

      {step === 'stores' && <div className="grid gap-3">
        <p className="text-sm text-muted-foreground">Tick the stores the agent may shop at. Connecting an account lets it fill that store’s real cart.</p>
        <StoreAccounts online={account.online} picked={allowed} onPick={(id, on) => setAllowed((a) => ({ ...a, [id]: on }))} />
      </div>}

      {step === 'review' && review && <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <Row k="Shopping for" v={holder.trim() || 'Me'} />
        <Row k="Budget" v={`${money(review.period_limits[0].limit_minor)} per ${periodWord(review.period_limits[0].period)}`} />
        <Row k="Most per order" v={`${money(review.per_order_limit_minor)}, including fees`} />
        <Row k="Stores" v={review.allowed_merchant_ids.map(storeName).join(', ')} />
        <Row k="Never buy" v={review.blocked_categories.map(categoryLabel).join(', ') || 'Nothing blocked'} />
        <Row k="Ask me above" v={review.approval_above_minor == null ? 'Off' : money(review.approval_above_minor)} />
        <Row k="Unusual purchases" v={review.risk_review ? 'Reviewed' : 'Not reviewed'} />
        <Row k="Expires" v={`${new Date(review.expires_at).toLocaleDateString('en-HK', { dateStyle: 'medium', timeZone: 'Asia/Hong_Kong' })}`} />
      </dl>}

      <DialogFooter className="gap-2">
        {step !== 'rules' && <Button variant="ghost" disabled={busy} onClick={() => setStep(step === 'review' ? 'stores' : 'rules')}>Back</Button>}
        {step === 'rules' && <Button onClick={() => { setError(''); if (rulesValid()) setStep('stores'); }}>Next</Button>}
        {step === 'stores' && <Button onClick={toReview}>Review</Button>}
        {step === 'review' && <Button onClick={() => void activate()} disabled={busy || account.online === false}>
          {busy ? 'Activating…' : change ? 'Confirm and replace' : 'Confirm and activate'}</Button>}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}

function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: React.ReactNode }) {
  return <div className="grid gap-1.5"><Label htmlFor={htmlFor}>{label}</Label>{children}</div>;
}

function SwitchRow({ id, label, hint, checked, onChange }: { id: string; label: string; hint?: string; checked: boolean; onChange: (on: boolean) => void }) {
  return <div className="flex items-start justify-between gap-4">
    <Label htmlFor={id} className="grid gap-1 font-normal"><span className="font-medium">{label}</span>{hint && <span className="text-xs text-muted-foreground">{hint}</span>}</Label>
    <Switch id={id} checked={checked} onCheckedChange={onChange} />
  </div>;
}

function Row({ k, v }: { k: string; v: string }) {
  return <><dt className="text-muted-foreground">{k}</dt><dd className="font-medium">{v}</dd></>;
}
