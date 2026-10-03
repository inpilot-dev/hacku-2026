import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ApprovalRequest, CatalogResponse, PaymentOptionsResponse, Quote, Receipt, RiskAssessment, RuleViolation, ShoppingItem, TranscriptionRequest } from '../../../../../contracts/types';
import { api, ApiError } from '@/lib/api';
import { money } from '@/lib/format';
import { newId } from '@/lib/utils';
import { TOKEN, useAccount, type Who } from './account';

/*
 * Repeat grocery shopping under the allowance, moved from the single-page flow (simple/SimpleApp.tsx) with its
 * behaviour unchanged: the agent builds a basket, the wallet (Kip) pays, refuses or asks you. A checkout whose
 * answer was lost is recovered from its saved transaction and never submitted twice.
 */

const STORE_ID = 'wellcome';
const PICKUP_CONTEXT_ID = 'ctx_wellcome_click_collect';
const PENDING_CHECKOUT_KEY = 'mandate-pending-checkout-v1';
const PRESETS_KEY = 'mandate-shopping-presets-v1';

export type Line = { product_id: string; quantity: number };
export type Preset = { id: string; title: string; subtitle: string; items: Line[]; list: string[]; intent?: ShoppingItem[]; edited?: boolean; ruleTest?: boolean };
type SavedPreset = { title: string; items: ShoppingItem[] };
export type Verdict =
  | { kind: 'paid'; receipt: Receipt; replayed: boolean }
  | { kind: 'refused'; message: string; violations: RuleViolation[] }
  | { kind: 'review'; message: string; violations: RuleViolation[]; approval: ApprovalRequest; risk?: RiskAssessment | null }
  | { kind: 'uncertain'; message: string };
export type Phase = 'pick' | 'packing' | 'basket' | 'paying' | 'verdict';
export type ChatMessage = { id: string; who: Who; text: string; tone: 'good' | 'bad' | 'info' };
type Spoken = { kind: 'list'; items: ShoppingItem[] };

export const PRESETS: Preset[] = [
  {
    id: 'basics', title: 'Weekly basics', subtitle: 'Rice, milk, greens, apples, tea',
    items: [
      { product_id: 'wellcome_101322993', quantity: 1 },
      { product_id: 'wellcome_101355093', quantity: 1 },
      { product_id: 'wellcome_101378087', quantity: 1 },
      { product_id: 'wellcome_102127176', quantity: 3 },
      { product_id: 'wellcome_101343395', quantity: 1 },
    ],
    list: ['jasmine rice 5kg', 'fresh milk 1L', 'broccoli', '3 apples', 'no-sugar jasmine tea'],
  },
  {
    id: 'fruit', title: 'Fruit & tea run', subtitle: 'Grapes, blueberries, kiwis, oolong',
    items: [
      { product_id: 'wellcome_101869136', quantity: 1 },
      { product_id: 'wellcome_101374428', quantity: 1 },
      { product_id: 'wellcome_101373345', quantity: 2 },
      { product_id: 'wellcome_101343041', quantity: 1 },
    ],
    list: ['shine muscat grapes', 'blueberries', '2 kiwis', 'no-sugar oolong tea'],
  },
  {
    id: 'champagne', title: 'Sneak in champagne', subtitle: 'Tests the rules: refused if alcohol is blocked', ruleTest: true,
    items: [
      { product_id: 'wellcome_113277654', quantity: 1 },
      { product_id: 'wellcome_101355093', quantity: 1 },
    ],
    list: ['champagne case', 'fresh milk 1L'],
  },
];

const REASONS: Partial<Record<RuleViolation['code'], string>> = {
  ORDER_CAP_EXCEEDED: 'Over the per-order limit',
  PERIOD_BUDGET_EXCEEDED: 'Over this period’s budget',
  MERCHANT_NOT_ALLOWED: 'That shop isn’t allowed',
  CATEGORY_BLOCKED: 'Something in the basket is off-limits',
  CATEGORY_REVIEW_REQUIRED: 'This one needs your OK',
  MANDATE_NOT_ACTIVE: 'The allowance isn’t active',
  MANDATE_EXPIRED: 'The allowance has expired',
  MANDATE_REVOKED: 'The allowance is revoked',
  RISK_REVIEW_REQUIRED: 'Something looks unusual',
};

// Risk signals by check name (the part of the rule id after "risk:"), matching services/api/mandate/payments/risk.py.
const RISK_SIGNALS: Record<string, string> = {
  large_basket: 'Bigger than usual',
  near_cap: 'Close to the order limit',
  new_merchant: 'New shop',
  new_items: 'Never bought before',
  price_jump: 'Price jumped',
  split_order: 'Looks like a split order',
  burst: 'Many orders at once',
  budget_burn: 'Budget going fast',
  odd_hour: 'Unusual time',
  listing_text: 'Listing talks to the agent',
};

export const riskCheck = (v: RuleViolation) => v.code === 'RISK_REVIEW_REQUIRED' ? v.rule_id.split('/risk:')[1] ?? null : null;
export const reasonTitle = (v: RuleViolation) => {
  const check = riskCheck(v);
  const signal = check ? RISK_SIGNALS[check.split(':')[0]] : undefined;
  return signal ?? REASONS[v.code] ?? v.code.replace(/_/g, ' ').toLowerCase();
};

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '');
    reader.onerror = () => reject(reader.error ?? new Error('Could not read the recording.'));
    reader.readAsDataURL(blob);
  });
}

function audioFormat(mimeType: string): TranscriptionRequest['format'] {
  if (mimeType.includes('ogg')) return 'ogg';
  if (mimeType.includes('webm')) return 'webm';
  if (mimeType.includes('mp4') || mimeType.includes('aac')) return 'm4a';
  return 'ogg';
}

function savedPresets(): Record<string, SavedPreset> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(PRESETS_KEY) ?? '{}');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    const result: Record<string, SavedPreset> = {};
    for (const [id, raw] of Object.entries(value)) {
      if ((!PRESETS.some((preset) => preset.id === id) && !/^custom-[\w-]{1,80}$/.test(id)) || !raw || typeof raw !== 'object') continue;
      const candidate = raw as Partial<SavedPreset>;
      if (typeof candidate.title !== 'string' || !candidate.title.trim() || !Array.isArray(candidate.items)) continue;
      const items = candidate.items.flatMap((entry): ShoppingItem[] => {
        if (!entry || typeof entry !== 'object') return [];
        const item = entry as Partial<ShoppingItem>;
        if (typeof item.name !== 'string' || !item.name.trim() || !Number.isInteger(item.quantity) || Number(item.quantity) < 1 || Number(item.quantity) > 20) return [];
        return [{ name: item.name.trim().slice(0, 100), quantity: Number(item.quantity), unit: typeof item.unit === 'string' ? item.unit.trim().slice(0, 24) || null : null }];
      }).slice(0, 20);
      if (items.length) result[id] = { title: candidate.title.trim().slice(0, 60), items };
    }
    return result;
  } catch { return {}; }
}

const describe = (items: ShoppingItem[]) => items.map((item) => `${item.quantity > 1 ? `${item.quantity} ` : ''}${item.name}${item.unit ? ` ${item.unit}` : ''}`).join(', ');
const parseRaw = (raw: string): ShoppingItem => {
  const match = raw.match(/^\s*(\d+)\s+(.+)$/);
  return match ? { name: match[2], quantity: Number(match[1]) } : { name: raw, quantity: 1 };
};

export function useGroceries() {
  const account = useAccount();
  const { mandate, active, card, loaded, mandateId, refresh, setCheckoutLocked } = account;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [phase, setPhase] = useState<Phase>('pick');
  const [preset, setPreset] = useState<Preset | null>(null);
  const [custom, setCustom] = useState<Record<string, number>>({});
  const [quote, setQuote] = useState<Quote | null>(null);
  const [paymentComparison, setPaymentComparison] = useState<PaymentOptionsResponse | null>(null);
  const [routeId, setRouteId] = useState<string | null>(null);
  const [routeLabel, setRouteLabel] = useState('');
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [ruleTestBasket, setRuleTestBasket] = useState(false);
  const [agentNote, setAgentNote] = useState('');
  const [listText, setListText] = useState('');
  const [voice, setVoice] = useState<'' | 'recording' | 'transcribing'>('');
  const recorderRef = useRef<MediaRecorder | null>(null);
  const [saved, setSaved] = useState<Record<string, SavedPreset>>(savedPresets);

  const say = useCallback((who: Who, text: string, tone: ChatMessage['tone'] = 'info') => {
    setMessages((current) => current[current.length - 1]?.who === who && current[current.length - 1]?.text === text ? current : [...current, { id: newId(), who, text, tone }]);
  }, []);
  const accountNote = account.note;
  const note = useCallback((who: Who, text: string, tone: ChatMessage['tone']) => {
    say(who, text, tone);
    accountNote(who, text, tone);
  }, [say, accountNote]);

  useEffect(() => { api.catalog(TOKEN).then(setCatalog).catch(() => setCatalog(null)); }, []);

  // An unfinished checkout from an earlier visit is resolved from its saved transaction before anything else.
  useEffect(() => {
    if (!loaded || !mandateId) return;
    let pending: { mandateId: string; quoteId: string; transactionId: string };
    try {
      const stored = JSON.parse(sessionStorage.getItem(PENDING_CHECKOUT_KEY) ?? 'null');
      if (!stored || stored.mandateId !== mandateId || typeof stored.quoteId !== 'string' || typeof stored.transactionId !== 'string') return;
      pending = stored;
    } catch { return; }
    let cancelled = false;
    setRecoveryBlocked(true);
    setBusy('checkout-restore');
    void (async () => {
      try {
        const restoredQuote = await api.quoteById(TOKEN, pending.quoteId);
        if (cancelled) return;
        setRecoveryBlocked(false);
        setQuote(restoredQuote); setPhase('verdict');
        sessionStorage.setItem(`mandate-tx-${restoredQuote.id}`, pending.transactionId);
        try {
          const receipt = await api.paymentByTransaction(TOKEN, pending.transactionId);
          if (cancelled) return;
          setVerdict({ kind: 'paid', receipt, replayed: true });
          account.receipts.remember(receipt, restoredQuote);
          sessionStorage.removeItem(PENDING_CHECKOUT_KEY);
          note('kip', 'Restored the confirmed receipt for your previous checkout. No new payment was submitted.', 'good');
        } catch {
          if (!cancelled) setVerdict({ kind: 'uncertain', message: 'Restored an unfinished checkout. Its payment status is unknown. Check status using the saved transaction before shopping again.' });
        }
      } catch {
        if (!cancelled) setError('An unfinished checkout could not be restored. Check its transaction in the full dashboard before making another purchase.');
      } finally { if (!cancelled) setBusy(''); }
    })();
    return () => { cancelled = true; };
  }, [loaded, mandateId, note]);

  const products = useMemo(() => catalog?.products.filter((p) => p.merchant_id === STORE_ID && p.available) ?? [], [catalog]);
  const productById = useMemo(() => new Map(products.map((p) => [p.id, p])), [products]);
  const catalogById = useMemo(() => new Map((catalog?.products ?? []).map((p) => [p.id, p])), [catalog]);
  const snapshotAt = useMemo(() => {
    const times = catalog?.evidence.filter((e) => e.kind === 'product_price').map((e) => e.observed_at).sort() ?? [];
    return times.length ? new Date(times[times.length - 1]).toLocaleString('en-HK', { dateStyle: 'medium', timeStyle: 'short' }) : null;
  }, [catalog]);

  const canSpend = Boolean(active && card?.status !== 'frozen' && !recoveryBlocked && busy !== 'checkout-restore');
  const inFlight = recoveryBlocked || busy === 'checkout-restore' || phase === 'paying' || verdict?.kind === 'uncertain';
  useEffect(() => { setCheckoutLocked(inFlight); }, [inFlight, setCheckoutLocked]);

  function resetShop() {
    // A deliberate new shopping request is distinct from retrying an in-flight request.
    sessionStorage.removeItem('mandate-idempotency-agent-run');
    setPaymentComparison(null); setRuleTestBasket(false); setPhase('pick'); setPreset(null); setQuote(null); setVerdict(null); setAgentNote(''); setRouteId(null); setRouteLabel('');
  }
  // Freezing or revoking (from the wallet tab) ends the current basket; a new allowance starts a fresh one.
  useEffect(() => { if (!canSpend && phase === 'basket') resetShop(); }, [canSpend, phase]);
  const firstMandate = useRef(mandateId);
  useEffect(() => {
    if (firstMandate.current === mandateId) return;
    firstMandate.current = mandateId;
    resetShop(); setMessages([]);
  }, [mandateId]);

  /** Ask the agent service first; if it isn't mounted, use the preset basket and say so. */
  async function packWithAgent(list: ShoppingItem[]): Promise<{ quote: Quote | null; message: string }> {
    if (!mandate) return { quote: null, message: '' };
    try {
      const run = await api.startAgentRun(TOKEN, { mandate_id: mandate.id, shopping_list: list, auto_purchase: false });
      let current = run;
      if (run.message) note('kumi', run.message, 'info');
      for (let tries = 0; tries < 20 && !current.quote_id && !['failed', 'refused', 'completed'].includes(current.status); tries += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        const next = await api.agentRun(TOKEN, run.id);
        if (next.message && next.message !== current.message) note('kumi', next.message, 'info');
        current = next;
      }
      if (current.quote_id) {
        setAgentNote(`Packed by ${current.provider === 'jev' ? 'the shopping agent' : current.provider}`);
        return { quote: await api.quoteById(TOKEN, current.quote_id), message: current.message };
      }
      return { quote: null, message: current.message || 'The agent could not build a basket.' };
    } catch {
      return { quote: null, message: '' };  // agent service not reachable
    }
  }

  async function toggleRecording() {
    if (voice === 'recording') { recorderRef.current?.stop(); return; }
    if (voice) return;
    setError('');
    let stream: MediaStream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch {
      setError('Microphone access was blocked. Allow it for this page, or type the list.'); return;
    }
    const mimeType = ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find((type) => MediaRecorder.isTypeSupported(type)) ?? '';
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: Blob[] = [];
    recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
    recorder.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      void transcribe(new Blob(chunks, { type: recorder.mimeType }));
    };
    recorderRef.current = recorder;
    recorder.start();
    setVoice('recording');
    window.setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, 30000);
  }

  async function transcribe(blob: Blob) {
    setVoice('transcribing');
    try {
      const result = await api.transcribeShoppingList(TOKEN, { audio_base64: await blobToBase64(blob), format: audioFormat(blob.type) });
      if (!result.text.trim()) setError('No words came through. Try again, or type the list.');
      else setListText((current) => (current.trim() ? `${current.trim()}\n` : '') + result.text.trim());
    } catch (err) {
      setError(err instanceof Error ? `Could not transcribe that: ${err.message}` : 'Could not transcribe that.');
    } finally { setVoice(''); }
  }

  async function shopFromText() {
    const text = listText.trim();
    if (!mandate || !canSpend || !text || busy) return;
    say('you', text);
    setListText('');
    setError(''); setBusy('parse');
    try {
      const parsed = await api.parseShoppingList(TOKEN, { text });
      if (!parsed.items.length) { setError('No items found in that. Try “rice, milk, 3 apples”.'); setListText(text); return; }
      note('kumi', `Heard: ${parsed.items.map((item) => `${item.name}${item.quantity > 1 ? ` ×${item.quantity}` : ''}`).join(', ')}${parsed.source === 'rules' ? ' (split by simple rules)' : ''}`, 'info');
      await shop({ kind: 'list', items: parsed.items });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read that list.');
      setListText(text);
    } finally { setBusy(''); }
  }

  async function shop(chosen: Preset | 'custom' | Spoken) {
    if (!mandate || !canSpend || phase === 'packing' || phase === 'paying') return;
    if (chosen === 'custom') say('you', 'Price the items I picked from the shelf.');
    else if (!('kind' in chosen)) say('you', `Shop for ${chosen.title}: ${chosen.list.join(', ')}.`);
    const spoken = chosen !== 'custom' && 'kind' in chosen;
    const editedPreset = !spoken && chosen !== 'custom' && chosen.edited === true;
    const lines = chosen === 'custom'
      ? Object.entries(custom).filter(([, q]) => q > 0).map(([product_id, quantity]) => ({ product_id, quantity }))
      : spoken ? [] : chosen.items.filter((line) => productById.has(line.product_id));
    if (!spoken && !lines.length && !editedPreset) { setError('That basket is empty in the current catalog.'); return; }
    setPaymentComparison(null); setRouteId(null); setRouteLabel('');
    setRuleTestBasket(false);
    setError(''); setVerdict(null); setPreset(chosen === 'custom' || spoken ? null : chosen); setPhase('packing');
    try {
      let result: Quote | null = null;
      if (chosen !== 'custom') {
        const packed = await packWithAgent(spoken ? chosen.items : chosen.intent ?? chosen.list.map((name) => ({ name, quantity: 1 })));
        result = packed.quote;
        if (!result && (spoken || editedPreset)) {
          setError(packed.message || (editedPreset ? 'The shopping agent isn’t connected. Your edited list was not replaced with the old preset.' : 'The shopping agent isn’t connected, so the list can’t be read right now.'));
          if (packed.message) note('kumi', packed.message, 'bad');
          setPhase('pick');
          return;
        }
        if (!result) {
          setRuleTestBasket(true);
          // The preset still goes to the wallet, so its own check is shown either way.
          if (packed.message) note('kumi', packed.message, 'bad');
          setAgentNote(packed.message ? 'The agent wouldn’t pack it · preset sent to the wallet as a rule test' : 'Preset basket · agent not connected here');
        }
      }
      if (!result) {
        if (chosen === 'custom') setAgentNote('Picked by you');
        result = await api.quote(TOKEN, { merchant_id: STORE_ID, delivery_context_id: PICKUP_CONTEXT_ID, items: lines });
      }
      setQuote(result);
      note('kumi', `Basket ready: ${result.items.reduce((n, item) => n + item.quantity, 0)} items · ${money(result.total_minor)}.`, 'info');
      try {
        const options = await api.paymentOptions(TOKEN, result.id);
        setPaymentComparison(options);
        const chosenRoute = options.options.find((o) => o.eligible && o.route_id === options.recommended_route_id) ?? options.options.find((o) => o.eligible);
        setRouteId(chosenRoute?.route_id ?? null); setRouteLabel(chosenRoute?.label ?? '');
      } catch { setRouteId(null); setRouteLabel(''); }
      setPhase('basket');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not price that basket.');
      setPhase('pick');
    }
  }

  async function checkout(approvalId?: string) {
    if (!mandate || !quote || phase === 'paying') return;
    say('you', approvalId ? 'Proceed with this approved basket.' : `Check the rules and pay ${money(quote.total_minor)} in the sandbox.`);
    setPhase('paying'); setError('');
    const key = `mandate-tx-${quote.id}`;
    const transactionId = sessionStorage.getItem(key) ?? newId();
    sessionStorage.setItem(key, transactionId);
    sessionStorage.setItem(PENDING_CHECKOUT_KEY, JSON.stringify({ mandateId: mandate.id, quoteId: quote.id, transactionId }));
    await new Promise((resolve) => window.setTimeout(resolve, 600)); // the wallet's check reads as a moment, not a flicker
    try {
      const result = await api.demoPurchase(TOKEN, { mandate_id: mandate.id, quote_id: quote.id, transaction_id: transactionId, payment_route_id: routeId, approval_id: approvalId ?? null });
      const auth = result.authorization;
      if (auth.status === 'requires_review' && auth.approval_request) {
        sessionStorage.removeItem(PENDING_CHECKOUT_KEY);
        setVerdict({ kind: 'review', message: auth.message, violations: auth.violations, approval: auth.approval_request, risk: auth.risk_assessment });
        note('kip', `Your approval is needed for ${money(quote.total_minor)}: ${auth.message}. Nothing is paid while this waits.`, 'info');
      } else if (auth.status !== 'approved') {
        sessionStorage.removeItem(PENDING_CHECKOUT_KEY);
        setVerdict({ kind: 'refused', message: auth.message, violations: auth.violations });
        note('kip', `Purchase blocked. ${[...new Set([auth.message, ...auth.violations.map((v) => v.message)])].join(' ')} Nothing was paid.`, 'bad');
      } else if (result.payment?.status === 'completed') {
        sessionStorage.removeItem(PENDING_CHECKOUT_KEY);
        setVerdict({ kind: 'paid', receipt: result.payment.receipt, replayed: result.payment.replayed });
        account.receipts.remember(result.payment.receipt, quote);
        note('kip', `Sandbox payment confirmed: ${money(result.payment.receipt.amount_minor)}. No real retailer payment was submitted.`, 'good');
      } else if (result.payment?.status === 'refused') {
        sessionStorage.removeItem(PENDING_CHECKOUT_KEY);
        setVerdict({ kind: 'refused', message: result.payment.message, violations: result.payment.violations });
        note('kip', `Payment refused: ${result.payment.message}. Nothing was paid.`, 'bad');
      } else {
        note('kip', 'The wallet approved the request, but payment is not confirmed. Check its status before making another purchase.', 'info');
        setVerdict({ kind: 'uncertain', message: 'The wallet approved it but hasn’t confirmed payment yet.' });
      }
    } catch (err) {
      // A transport/HTTP error is not proof that the wallet did not capture payment.
      note('kip', `Checkout response unavailable${err instanceof Error ? `: ${err.message}` : ''}. Checking the saved transaction.`, 'info');
      try {
        const receipt = await api.paymentByTransaction(TOKEN, transactionId);
        sessionStorage.removeItem(PENDING_CHECKOUT_KEY);
        setVerdict({ kind: 'paid', receipt, replayed: true });
        account.receipts.remember(receipt, quote);
        note('kip', `Recovered the confirmed sandbox receipt ${receipt.id}. No second payment was submitted.`, 'good');
      } catch {
        setVerdict({ kind: 'uncertain', message: 'Payment status is unknown. Check status to look up the saved transaction; this does not submit another payment. A missing receipt is not proof of failure.' });
      }
    }
    setPhase('verdict');
    void refresh(mandate.id);
  }

  async function checkPaymentStatus() {
    if (!quote || verdict?.kind !== 'uncertain' || busy) return;
    const transactionId = sessionStorage.getItem(`mandate-tx-${quote.id}`);
    if (!transactionId) { setError('The transaction reference is missing. Resolve the purchase in the full dashboard before shopping again.'); return; }
    setBusy('payment-status'); setError('');
    try {
      const receipt = await api.paymentByTransaction(TOKEN, transactionId);
      sessionStorage.removeItem(PENDING_CHECKOUT_KEY);
      setVerdict({ kind: 'paid', receipt, replayed: true });
      account.receipts.remember(receipt, quote);
      note('kip', `Payment confirmed from receipt ${receipt.id}. This status check did not submit a payment.`, 'good');
      void refresh();
    } catch (err) {
      const message = err instanceof ApiError && err.status === 404
        ? 'No completed receipt is available yet. Payment status remains unknown. No payment was submitted by this check.'
        : 'Could not retrieve payment status. No payment was submitted by this check.';
      setVerdict({ kind: 'uncertain', message });
      note('kip', message, 'info');
    } finally { setBusy(''); }
  }

  async function decide(approve: boolean) {
    if (verdict?.kind !== 'review' || busy === 'decide') return;
    say('you', approve ? 'Approve this basket once.' : 'Decline this purchase.');
    setBusy('decide');
    try {
      if (approve) {
        await api.approve(TOKEN, verdict.approval.id);
        note('bean', 'You approved it once', 'good');
        await checkout(verdict.approval.id);
      } else {
        await api.deny(TOKEN, verdict.approval.id);
        note('bean', 'You said no', 'info');
        setVerdict({ kind: 'refused', message: 'You declined this one. Nothing was paid.', violations: [] });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not record your decision.');
    } finally { setBusy(''); }
  }

  // ------------------------------------------------------------------------------------------- presets

  const presets: Preset[] = useMemo(() => PRESETS.map((base) => {
    const own = saved[base.id];
    return own ? { ...base, title: own.title, subtitle: describe(own.items), list: own.items.map((item) => item.name), intent: own.items, edited: true } : base;
  }).concat(Object.entries(saved).filter(([id]) => id.startsWith('custom-')).map(([id, own]) => ({
    id, title: own.title, subtitle: describe(own.items), items: [], list: own.items.map((item) => item.name), intent: own.items, edited: true,
  }))), [saved]);

  function persist(next: Record<string, SavedPreset>): string | null {
    try { localStorage.setItem(PRESETS_KEY, JSON.stringify(next)); } catch { return 'Could not save on this device. Check available storage and try again.'; }
    setSaved(next);
    return null;
  }

  /** Validated save of a preset's name and list; returns an error message, or null when saved. */
  function savePreset(id: string, title: string, rawItems: ShoppingItem[], asCopy = false): string | null {
    const name = title.trim();
    const items = rawItems.map((item) => ({ name: item.name.trim(), quantity: Number(item.quantity), unit: item.unit?.trim() || null })).filter((item) => item.name);
    if (!name) return 'Give this preset a name.';
    if (!items.length || items.length > 20) return 'Add between 1 and 20 items.';
    if (items.some((item) => !Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 20)) return 'Each quantity must be between 1 and 20.';
    if (asCopy) return persist({ ...saved, [`custom-${newId()}`]: { title: `${name} copy`.slice(0, 60), items } });
    return persist({ ...saved, [id]: { title: name.slice(0, 60), items } });
  }

  function resetPreset(id: string): string | null {
    const next = { ...saved };
    delete next[id];
    return persist(next);
  }

  function presetDraft(target: Preset): { title: string; items: ShoppingItem[] } {
    const own = saved[target.id];
    return { title: own?.title ?? target.title, items: own?.items.map((item) => ({ ...item })) ?? target.list.map(parseRaw) };
  }

  const estimate = (p: Preset) => p.items.reduce((sum, line) => sum + (productById.get(line.product_id)?.unit_price_minor ?? 0) * line.quantity, 0);
  const customCount = Object.values(custom).reduce((sum, q) => sum + q, 0);
  const customTotal = Object.entries(custom).reduce((sum, [id, q]) => sum + (productById.get(id)?.unit_price_minor ?? 0) * q, 0);

  return {
    messages, error, setError, busy, phase, preset, quote, verdict, ruleTestBasket, agentNote, recoveryBlocked, canSpend, inFlight,
    paymentComparison, routeId, routeLabel, selectRoute: (id: string, label: string) => { setRouteId(id); setRouteLabel(label); },
    listText, setListText, voice, toggleRecording, shopFromText, shop, checkout, checkPaymentStatus, decide, resetShop,
    products, productById, catalogById, snapshotAt, custom, setCustom, customCount, customTotal,
    presets, presetDraft, savePreset, resetPreset, isDefaultPreset: (id: string) => PRESETS.some((p) => p.id === id), estimate,
  };
}

export type Groceries = ReturnType<typeof useGroceries>;
