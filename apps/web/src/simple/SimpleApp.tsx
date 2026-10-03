import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { LucideIcon } from 'lucide-react';
import { ArrowRight, Carrot, CreditCard, Cherry, Mic, Pencil, Printer, ReceiptText, RotateCcw, Snowflake, Square, Trash2, Wine, X, UserRound } from 'lucide-react';
import './openai-tokens.css';
import ReactiveCharacter from '../components/ReactiveCharacter';
import type { ApprovalRequest, AuditEvent, BudgetResponse, CatalogResponse, Mandate, Product, Quote, Receipt, RuleViolation, ShoppingItem, TranscriptionRequest, VirtualCard } from '../../../../contracts/types';
import { api, ApiError } from '../lib/api';
import { categoryLabel, money, periodWord } from '../lib/format';
import { holderName, possessive, saveHolderName } from './holder';
import CartSyncPanel from './CartSyncPanel';
import Onboarding, { storeName } from './Onboarding';
import ProfileSheet from './ProfileSheet';
import MessageList, { type ConversationMessage } from '../shopping/MessageList';
import '../shopping/conversation.css';

/*
 * A single-screen take on the core Mandate flow, for whoever the shopping is for (a parent, a teen, a flat or yourself):
 * set an allowance, let Kumi fill a basket, watch Kip (the wallet) pay or refuse, freeze it any time.
 * Copy follows the confirmed policy (period, blocked categories, stores), never a fixed scenario.
 * The wallet API stays the only authority; this screen never decides anything itself.
 */

const TOKEN = 'dev-user-token';
const STORE_ID = 'wellcome';
const PICKUP_CONTEXT_ID = 'ctx_wellcome_click_collect';
const MANDATE_KEY = 'mandate-id';
const PRESETS_KEY = 'mandate-shopping-presets-v1';

type Mascot = 'kumi' | 'kip' | 'bean' | 'stella';
type Line = { product_id: string; quantity: number };
type Pick = { id: string; icon: LucideIcon; title: string; subtitle: string; tone: 'green' | 'orange' | 'pink'; items: Line[]; list: string[]; intent?: ShoppingItem[]; edited?: boolean };
type SavedPreset = { title: string; items: ShoppingItem[] };
type ReceiptRecord = { receipt: Receipt; quote: Quote | null; occurredAt: string };
type Verdict =
  | { kind: 'paid'; receipt: Receipt; replayed: boolean }
  | { kind: 'refused'; message: string; violations: RuleViolation[] }
  | { kind: 'review'; message: string; violations: RuleViolation[]; approval: ApprovalRequest }
  | { kind: 'uncertain'; message: string };
type Phase = 'pick' | 'packing' | 'basket' | 'paying' | 'verdict';
type LogEntry = { id: string; at: string; who: Mascot; text: string; tone: 'good' | 'bad' | 'info'; state?: string };

const PICKS: Pick[] = [
  {
    id: 'basics', icon: Carrot, title: 'Weekly basics', subtitle: 'Rice, milk, greens, apples, tea', tone: 'green',
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
    id: 'fruit', icon: Cherry, title: 'Fruit & tea run', subtitle: 'Grapes, blueberries, kiwis, oolong', tone: 'orange',
    items: [
      { product_id: 'wellcome_101869136', quantity: 1 },
      { product_id: 'wellcome_101374428', quantity: 1 },
      { product_id: 'wellcome_101373345', quantity: 2 },
      { product_id: 'wellcome_101343041', quantity: 1 },
    ],
    list: ['shine muscat grapes', 'blueberries', '2 kiwis', 'no-sugar oolong tea'],
  },
  {
    id: 'champagne', icon: Wine, title: 'Sneak in champagne', subtitle: 'Test the rules: refused if alcohol is blocked', tone: 'pink',
    items: [
      { product_id: 'wellcome_113277654', quantity: 1 },
      { product_id: 'wellcome_101355093', quantity: 1 },
    ],
    list: ['champagne case', 'fresh milk 1L'],
  },
];

const REASONS: Partial<Record<RuleViolation['code'], string>> = {
  ORDER_CAP_EXCEEDED: 'Over the per-order limit',
  PERIOD_BUDGET_EXCEEDED: 'Would blow this week’s budget',
  MERCHANT_NOT_ALLOWED: 'That shop isn’t on the list',
  CATEGORY_BLOCKED: 'Something in the basket is off-limits',
  CATEGORY_REVIEW_REQUIRED: 'This one needs your OK',
  MANDATE_NOT_ACTIVE: 'The card isn’t active',
  MANDATE_EXPIRED: 'The allowance has expired',
  MANDATE_REVOKED: 'The card is frozen',
  RISK_REVIEW_REQUIRED: 'Kip spotted something unusual',
};

function Avatar({ name, state, size = 64 }: { name: Mascot; state: string; size?: number }) {
  return <ReactiveCharacter className="m2-avatar" name={name} state={state} size={size} />;
}

function Sticker({ name, state, size = 88, tilt = -6 }: { name: Mascot; state: string; size?: number; tilt?: number }) {
  return <span className={`m2-sticker ${name}`} style={{ width: size, height: size, transform: `rotate(${tilt}deg)` }}><ReactiveCharacter name={name} state={state} size={size} /></span>;
}

const hkd = (minor: number) => money(minor).replace('HK$', 'HK$\u202F');
const merchantName = (id: string) => storeName(id) !== id ? storeName(id) : id.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());

function eventText(event: AuditEvent): Omit<LogEntry, 'id' | 'at'> | null {
  const amount = typeof event.payload.amount_minor === 'number' ? ` ${money(event.payload.amount_minor)}` : '';
  switch (event.type) {
    case 'mandate_confirmed': return { who: 'bean', text: 'Allowance switched on', tone: 'good' };
    case 'mandate_revoked': return { who: 'kip', text: 'Allowance permanently revoked', tone: 'bad', state: 'revoked' };
    case 'card_frozen': return { who: 'kip', text: 'Virtual card paused', tone: 'bad', state: 'revoked' };
    case 'card_unfrozen': return { who: 'kip', text: 'Virtual card resumed', tone: 'good', state: 'idle' };
    case 'quote_created': return { who: 'kumi', text: 'Kumi priced a basket', tone: 'info' };
    case 'authorization_refused': return { who: 'kip', text: 'Kip refused a purchase', tone: 'bad' };
    case 'payment_completed': return { who: 'kip', text: `Kip paid${amount}`, tone: 'good' };
    case 'payment_refused': return { who: 'kip', text: 'Payment refused', tone: 'bad' };
    default: return null;
  }
}

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
      if ((!PICKS.some((pick) => pick.id === id) && !/^custom-[\w-]{1,80}$/.test(id)) || !raw || typeof raw !== 'object') continue;
      const candidate = raw as Partial<SavedPreset>;
      if (typeof candidate.title !== 'string' || !candidate.title.trim() || !Array.isArray(candidate.items)) continue;
      const items = candidate.items.flatMap((raw): ShoppingItem[] => {
        if (!raw || typeof raw !== 'object') return [];
        const item = raw as Partial<ShoppingItem>;
        if (typeof item.name !== 'string' || !item.name.trim() || !Number.isInteger(item.quantity) || Number(item.quantity) < 1 || Number(item.quantity) > 20) return [];
        return [{ name: item.name.trim().slice(0, 100), quantity: Number(item.quantity), unit: typeof item.unit === 'string' ? item.unit.trim().slice(0, 24) || null : null }];
      }).slice(0, 20);
      if (items.length) result[id] = { title: candidate.title.trim().slice(0, 60), items };
    }
    return result;
  } catch { return {}; }
}

type Spoken = { kind: 'list'; items: ShoppingItem[] };

export default function SimpleApp() {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const say = useCallback((speaker: ConversationMessage['speaker'], text: string, tone: ConversationMessage['tone'] = 'info') => {
    setMessages((current) => [...current, { id: crypto.randomUUID(), speaker, text, tone }]);
  }, []);
  const [online, setOnline] = useState<boolean | null>(null);
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [mandateId, setMandateId] = useState(() => localStorage.getItem(MANDATE_KEY) ?? '');
  const [mandate, setMandate] = useState<Mandate | null>(null);
  const [budget, setBudget] = useState<BudgetResponse | null>(null);
  const [card, setCard] = useState<VirtualCard | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const [holder, setHolder] = useState(() => holderName(localStorage.getItem(MANDATE_KEY)));
  const [riskReviewOn, setRiskReviewOn] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);

  const [phase, setPhase] = useState<Phase>('pick');
  const [pick, setPick] = useState<Pick | null>(null);
  const [custom, setCustom] = useState<Record<string, number>>({});
  const [showCustom, setShowCustom] = useState(false);
  const [search, setSearch] = useState('');
  const [quote, setQuote] = useState<Quote | null>(null);
  const [routeId, setRouteId] = useState<string | null>(null);
  const [routeLabel, setRouteLabel] = useState('');
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [agentNote, setAgentNote] = useState('');
  const [listText, setListText] = useState('');
  const [voice, setVoice] = useState<'' | 'recording' | 'transcribing'>('');
  const recorderRef = useRef<MediaRecorder | null>(null);

  const [log, setLog] = useState<LogEntry[]>([]);
  const [serverLog, setServerLog] = useState<LogEntry[] | null>(null);
  const [showFine, setShowFine] = useState(false);
  const [presets, setPresets] = useState<Record<string, SavedPreset>>(savedPresets);
  const [editingPreset, setEditingPreset] = useState<Pick | null>(null);
  const [editorTitle, setEditorTitle] = useState('');
  const [editorItems, setEditorItems] = useState<ShoppingItem[]>([]);
  const [presetError, setPresetError] = useState('');
  const [receiptsOpen, setReceiptsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [receipts, setReceipts] = useState<ReceiptRecord[]>([]);
  const [receiptsLoading, setReceiptsLoading] = useState(false);
  const [receiptsError, setReceiptsError] = useState('');
  const [selectedReceipt, setSelectedReceipt] = useState<string | null>(null);
  const [receiptDetailLoading, setReceiptDetailLoading] = useState(false);
  const [receiptDetailError, setReceiptDetailError] = useState('');
  const receiptDetailRequestId = useRef(0);
  const receiptsLoaded = useRef(false);
  const receiptLoadInFlight = useRef(false);
  const shopRef = useRef<HTMLElement>(null);

  const note = useCallback((who: Mascot, text: string, tone: LogEntry['tone'], state?: string) => {
    say(who, text, tone);
    setLog((current) => [{ id: crypto.randomUUID(), at: new Date().toISOString(), who, text, tone, state }, ...current].slice(0, 20));
  }, [say]);

  const refresh = useCallback(async (id = mandateId) => {
    try { await api.health(); setOnline(true); } catch { setOnline(false); }
    if (!id) { setMandate(null); setBudget(null); setCard(null); setLoaded(true); return; }
    try {
      const [m, b] = await Promise.all([api.mandate(TOKEN, id), api.budget(TOKEN, id)]);
      setMandate(m); setBudget(b);
      try { setCard(await api.card(TOKEN, id)); } catch { setCard(null); }
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) { localStorage.removeItem(MANDATE_KEY); setMandateId(''); setMandate(null); setBudget(null); }
    } finally { setLoaded(true); }
    try {
      const result = await api.events(TOKEN, 0, 100);
      setServerLog(result.events.filter((event) => !event.mandate_id || event.mandate_id === id).reverse().slice(0, 12).flatMap((event) => {
        const text = eventText(event);
        return text ? [{ ...text, id: event.event_id, at: event.occurred_at }] : [];
      }));
    } catch { setServerLog(null); }
  }, [mandateId]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { api.catalog(TOKEN).then(setCatalog).catch(() => setCatalog(null)); }, []);

  const products = useMemo(() => catalog?.products.filter((p) => p.merchant_id === STORE_ID && p.available) ?? [], [catalog]);
  const productById = useMemo(() => new Map(products.map((p) => [p.id, p])), [products]);
  const catalogById = useMemo(() => new Map((catalog?.products ?? []).map((p) => [p.id, p])), [catalog]);
  const snapshotAt = useMemo(() => {
    const times = catalog?.evidence.filter((e) => e.kind === 'product_price').map((e) => e.observed_at).sort() ?? [];
    return times.length ? new Date(times[times.length - 1]).toLocaleString('en-HK', { dateStyle: 'medium', timeStyle: 'short' }) : null;
  }, [catalog]);

  const active = mandate?.status === 'active';
  const canSpend = active && card?.status !== 'frozen';
  const period = budget?.applicable_budgets[0];
  const available = period?.available_minor ?? mandate?.policy.period_limits[0]?.limit_minor ?? 0;
  const limit = period?.limit_minor ?? mandate?.policy.period_limits[0]?.limit_minor ?? 0;
  const spent = (period?.paid_minor ?? 0) + (period?.reserved_minor ?? 0);
  const ratio = limit ? Math.min(1, spent / limit) : 0;

  /** Onboarding confirmed a new mandate with the wallet; show it, and retire the one it replaces. */
  async function activated(result: Mandate, summary: string, name: string) {
    const replaced = mandate && mandate.status === 'active' && mandate.id !== result.id ? mandate : null;
    localStorage.setItem(MANDATE_KEY, result.id);
    saveHolderName(result.id, name); setHolder(name);
    setMandateId(result.id); setMandate(result); setSetupOpen(false); resetShop();
    note('bean', summary, 'good');
    if (replaced) {
      try {
        await api.revoke(TOKEN, replaced.id);
        sessionStorage.removeItem(`mandate-idempotency-revoke-${replaced.id}`);
        note('kip', 'Previous allowance frozen', 'info');
      } catch (err) {
        setError(`The new allowance is on, but the old one could not be frozen: ${err instanceof Error ? err.message : 'unknown error'}. Freeze it from the full dashboard.`);
      }
    }
    await refresh(result.id);
  }

  async function freeze() {
    if (!mandate) return;
    setBusy('freeze'); setError('');
    try {
      const result = await api.freezeCard(TOKEN, mandate.id);
      sessionStorage.removeItem(`mandate-idempotency-freeze-${mandate.id}`);
      setCard(result.card); resetShop();
      note('kip', 'Card paused. No new purchases can be made.', 'bad', 'frozen');
      await refresh(mandate.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not freeze the card.');
    } finally { setBusy(''); }
  }

  async function unfreeze() {
    if (!mandate) return;
    setBusy('unfreeze'); setError('');
    try {
      const result = await api.unfreezeCard(TOKEN, mandate.id);
      sessionStorage.removeItem(`mandate-idempotency-unfreeze-${mandate.id}`);
      setCard(result.card);
      note('kip', 'Card resumed. The existing allowance still applies.', 'good', 'active');
      await refresh(mandate.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not unfreeze the card.');
    } finally { setBusy(''); }
  }

  async function revokeAllowance() {
    if (!mandate || !window.confirm('Permanently revoke this allowance? Its virtual card will be cancelled and cannot be resumed.')) return;
    setBusy('revoke'); setError('');
    try {
      const result = await api.revoke(TOKEN, mandate.id);
      sessionStorage.removeItem(`mandate-idempotency-revoke-${mandate.id}`);
      setMandate(result.mandate); resetShop();
      note('kip', `Allowance revoked${result.cancelled_reservation_ids.length ? `, ${result.cancelled_reservation_ids.length} hold(s) released` : ''}`, 'bad', 'revoked');
      await refresh(mandate.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not revoke the allowance.');
    } finally { setBusy(''); }
  }

  function resetShop() {
    setPhase('pick'); setPick(null); setQuote(null); setVerdict(null); setAgentNote(''); setRouteId(null); setRouteLabel('');
  }

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
      return { quote: null, message: current.message || 'Kumi could not build a basket.' };
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
      if (!result.text.trim()) setError('Kumi didn’t catch any words. Try again, or type the list.');
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
      if (!parsed.items.length) { setError('Kumi couldn’t find any items in that. Try “rice, milk, 3 apples”.'); setListText(text); return; }
      note('kumi', `Heard: ${parsed.items.map((item) => `${item.name}${item.quantity > 1 ? ` ×${item.quantity}` : ''}`).join(', ')}${parsed.source === 'rules' ? ' (split by simple rules)' : ''}`, 'info');
      await shop({ kind: 'list', items: parsed.items });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kumi could not read that list.');
      setListText(text);
    } finally { setBusy(''); }
  }

  async function shop(chosen: Pick | 'custom' | Spoken) {
    if (!mandate || !canSpend || phase === 'packing' || phase === 'paying') return;
    if (chosen === 'custom') say('you', 'Price the items I picked from the shelf.');
    else if (!('kind' in chosen)) say('you', `Shop for ${chosen.title}: ${chosen.list.join(', ')}.`);
    const spoken = chosen !== 'custom' && 'kind' in chosen;
    const editedPreset = !spoken && chosen !== 'custom' && chosen.edited === true;
    const lines = chosen === 'custom'
      ? Object.entries(custom).filter(([, q]) => q > 0).map(([product_id, quantity]) => ({ product_id, quantity }))
      : spoken ? [] : chosen.items.filter((line) => productById.has(line.product_id));
    if (!spoken && !lines.length) { setError('That basket is empty in the current catalog.'); return; }
    setError(''); setVerdict(null); setPick(chosen === 'custom' || spoken ? null : chosen); setPhase('packing');
    try {
      let result: Quote | null = null;
      if (chosen !== 'custom') {
        const packed = await packWithAgent(spoken ? chosen.items : chosen.intent ?? chosen.list.map((name) => ({ name, quantity: 1 })));
        result = packed.quote;
        if (!result && (spoken || editedPreset)) {
          setError(packed.message || (editedPreset ? 'The shopping agent isn’t connected. Your edited list was not replaced with the old preset.' : 'The shopping agent isn’t connected, so Kumi can’t read a list right now.'));
          if (packed.message) note('kumi', packed.message, 'bad');
          setPhase('pick');
          return;
        }
        if (!result) {
          // The preset still goes to Kip, so the wallet's own check is shown either way.
          if (packed.message) note('kumi', packed.message, 'bad');
          setAgentNote(packed.message ? 'Kumi wouldn’t pack it · preset basket sent to Kip as a rule test' : 'Preset basket · agent not connected here');
        }
      }
      if (!result) {
        if (chosen === 'custom') setAgentNote('Picked by you');
        result = await api.quote(TOKEN, { merchant_id: STORE_ID, delivery_context_id: PICKUP_CONTEXT_ID, items: lines });
      }
      setQuote(result);
      note('kumi', `Kumi packed ${result.items.reduce((n, item) => n + item.quantity, 0)} items · ${money(result.total_minor)}`, 'info');
      try {
        const options = await api.paymentOptions(TOKEN, result.id);
        const chosenRoute = options.options.find((o) => o.route_id === options.recommended_route_id) ?? options.options.find((o) => o.eligible);
        setRouteId(chosenRoute?.route_id ?? null); setRouteLabel(chosenRoute?.label ?? '');
      } catch { setRouteId(null); setRouteLabel(''); }
      setPhase('basket');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kumi could not price that basket.');
      setPhase('pick');
    }
  }

  async function checkout(approvalId?: string) {
    if (!mandate || !quote || phase === 'paying') return;
    say('you', approvalId ? 'Proceed with this approved basket.' : `Check the rules and pay ${money(quote.total_minor)} in the sandbox.`);
    setPhase('paying'); setError('');
    const key = `mandate-tx-${quote.id}`;
    const transactionId = sessionStorage.getItem(key) ?? crypto.randomUUID();
    sessionStorage.setItem(key, transactionId);
    await new Promise((resolve) => window.setTimeout(resolve, 900)); // let Kip's check read as a moment, not a flicker
    try {
      const result = await api.demoPurchase(TOKEN, { mandate_id: mandate.id, quote_id: quote.id, transaction_id: transactionId, payment_route_id: routeId, approval_id: approvalId ?? null });
      const auth = result.authorization;
      if (auth.status === 'requires_review' && auth.approval_request) {
        setVerdict({ kind: 'review', message: auth.message, violations: auth.violations, approval: auth.approval_request });
        note('kip', `Your approval is needed for ${money(quote.total_minor)}: ${auth.message}. Nothing is paid while this waits.`, 'info');
      } else if (auth.status !== 'approved') {
        setVerdict({ kind: 'refused', message: auth.message, violations: auth.violations });
        note('kip', `Purchase blocked: ${auth.message}. ${auth.violations.map((v) => v.message).join(' ')} Nothing was paid.`, 'bad');
      } else if (result.payment?.status === 'completed') {
        setVerdict({ kind: 'paid', receipt: result.payment.receipt, replayed: result.payment.replayed });
        rememberReceipt(result.payment.receipt);
        note('kip', `Sandbox payment confirmed: ${money(result.payment.receipt.amount_minor)}. Receipt ${result.payment.receipt.id}. No real retailer payment was submitted.`, 'good');
      } else if (result.payment?.status === 'refused') {
        setVerdict({ kind: 'refused', message: result.payment.message, violations: result.payment.violations });
        note('kip', `Payment refused: ${result.payment.message}. Nothing was paid.`, 'bad');
      } else {
        setVerdict({ kind: 'uncertain', message: 'The wallet approved it but hasn’t confirmed payment yet.' });
      }
    } catch (err) {
      if (err instanceof ApiError && [401, 403, 404, 422, 503].includes(err.status)) {
        setVerdict({ kind: 'refused', message: `${err.message} No payment was made.`, violations: [] });
      } else {
        try {
          const receipt = await api.paymentByTransaction(TOKEN, transactionId);
          setVerdict({ kind: 'paid', receipt, replayed: true });
          rememberReceipt(receipt);
        } catch {
          setVerdict({ kind: 'uncertain', message: 'We lost the connection mid-checkout. Checking again reuses the same transaction, so it can’t pay twice.' });
        }
      }
    }
    receiptsLoaded.current = false;
    setPhase('verdict');
    void refresh(mandate.id);
  }

  async function decide(approve: boolean) {
    if (verdict?.kind !== 'review') return;
    if (busy === 'decide') return;
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

  const customCount = Object.values(custom).reduce((sum, q) => sum + q, 0);
  const customTotal = Object.entries(custom).reduce((sum, [id, q]) => sum + (productById.get(id)?.unit_price_minor ?? 0) * q, 0);
  const filtered = products.filter((p) => p.title.toLowerCase().includes(search.trim().toLowerCase())).slice(0, 24);
  const feed = serverLog && serverLog.length ? serverLog : log;
  const showSetup = loaded && (!mandate || setupOpen);
  // Phones pin Kumi's question and the list box to the bottom while picking (see simple.css).
  const docked = !showSetup && Boolean(mandate) && active && phase === 'pick';
  const visiblePicks = useMemo(() => PICKS.map((base) => {
    const saved = presets[base.id];
    return saved ? { ...base, title: saved.title, subtitle: saved.items.map((item) => `${item.quantity > 1 ? `${item.quantity} ` : ''}${item.name}${item.unit ? ` ${item.unit}` : ''}`).join(', '), list: saved.items.map((item) => item.name), intent: saved.items, edited: true } : base;
  }).concat(Object.entries(presets).filter(([id]) => id.startsWith('custom-')).map(([id, saved]) => ({
    id, icon: Carrot, title: saved.title, subtitle: saved.items.map((item) => `${item.quantity > 1 ? `${item.quantity} ` : ''}${item.name}${item.unit ? ` ${item.unit}` : ''}`).join(', '), tone: 'green' as const,
    items: [], list: saved.items.map((item) => item.name), intent: saved.items, edited: true,
  }))), [presets]);
  const selectedReceiptRecord = receipts.find((entry) => entry.receipt.id === selectedReceipt) ?? null;

  function editPreset(pickToEdit: Pick) {
    setPresetError('');
    const saved = presets[pickToEdit.id];
    setEditingPreset(pickToEdit);
    setEditorTitle(saved?.title ?? pickToEdit.title);
    setEditorItems(saved?.items.map((item) => ({ ...item })) ?? pickToEdit.list.map((raw) => {
      const match = raw.match(/^\s*(\d+)\s+(.+)$/);
      return match ? { name: match[2], quantity: Number(match[1]) } : { name: raw, quantity: 1 };
    }));
  }

  function savePreset() {
    if (!editingPreset) return;
    const title = editorTitle.trim();
    const items = editorItems.map((item) => ({ name: item.name.trim(), quantity: Number(item.quantity), unit: item.unit?.trim() || null })).filter((item) => item.name);
    if (!title) { setPresetError('Give this preset a name.'); return; }
    if (!items.length || items.length > 20) { setPresetError('Add between 1 and 20 items.'); return; }
    if (items.some((item) => !Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 20)) { setPresetError('Each quantity must be between 1 and 20.'); return; }
    const next = { ...presets, [editingPreset.id]: { title: title.slice(0, 60), items } };
    try { localStorage.setItem(PRESETS_KEY, JSON.stringify(next)); }
    catch { setPresetError('Could not save on this device. Check available storage and try again.'); return; }
    setPresets(next); setEditingPreset(null);
  }

  function resetPreset(id: string) {
    const next = { ...presets };
    delete next[id];
    try { localStorage.setItem(PRESETS_KEY, JSON.stringify(next)); }
    catch { setPresetError('Could not reset this preset on this device.'); return; }
    setPresets(next);
    const original = PICKS.find((item) => item.id === id);
    if (!original) { setEditingPreset(null); return; }
    setEditorTitle(original?.title ?? '');
    setEditorItems(original?.list.map((raw) => { const match = raw.match(/^\s*(\d+)\s+(.+)$/); return match ? { name: match[2], quantity: Number(match[1]) } : { name: raw, quantity: 1 }; }) ?? []);
  }

  function duplicatePreset() {
    if (!editingPreset) return;
    const title = editorTitle.trim();
    const items = editorItems.map((item) => ({ name: item.name.trim(), quantity: Number(item.quantity), unit: item.unit?.trim() || null })).filter((item) => item.name);
    if (!title || !items.length || items.length > 20 || items.some((item) => !Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 20)) {
      setPresetError('Add a name and 1–20 items with quantities from 1 to 20 before making a copy.'); return;
    }
    const id = `custom-${crypto.randomUUID()}`;
    const copy = { title: `${title} copy`.slice(0, 60), items };
    const next = { ...presets, [id]: copy };
    try { localStorage.setItem(PRESETS_KEY, JSON.stringify(next)); }
    catch { setPresetError('Could not duplicate this preset on this device.'); return; }
    setPresets(next); setEditingPreset(null);
  }

  async function loadReceipts(force = false) {
    if (receiptLoadInFlight.current || (receiptsLoaded.current && !force)) return;
    receiptLoadInFlight.current = true;
    setReceiptsLoading(true); setReceiptsError('');
    try {
      const audit = await api.auditExport(TOKEN);
      const paymentEvents = audit.events.filter((event) => event.type === 'payment_completed' && event.transaction_id);
      const transactionEvents = [...new Map(paymentEvents.map((event) => [event.transaction_id!, event])).values()];
      const records = await Promise.all(transactionEvents.map(async (event): Promise<ReceiptRecord | null> => {
        const payloadReceipt = event.payload.receipt as Receipt | undefined;
        if (payloadReceipt?.status === 'paid' && payloadReceipt.transaction_id === event.transaction_id) return { receipt: payloadReceipt, quote: null, occurredAt: event.occurred_at };
        try { return { receipt: await api.paymentByTransaction(TOKEN, event.transaction_id!), quote: null, occurredAt: event.occurred_at }; }
        catch { return null; }
      }));
      const ordered = records.filter((record): record is ReceiptRecord => record !== null).sort((a, b) => Date.parse(b.receipt.paid_at || b.occurredAt) - Date.parse(a.receipt.paid_at || a.occurredAt));
      setReceipts(ordered); receiptsLoaded.current = true;
    } catch (err) {
      setReceiptsError(err instanceof Error ? err.message : 'Could not load receipts.');
    } finally { receiptLoadInFlight.current = false; setReceiptsLoading(false); }
  }

  function showReceipts() {
    setReceiptsOpen(true); setSelectedReceipt(null); setReceiptDetailError(''); void loadReceipts();
  }

  function viewReceipt(record: ReceiptRecord) {
    setReceiptsOpen(true); setReceiptsError('');
    void loadReceipts();
    void openReceipt(record);
  }

  function rememberReceipt(receipt: Receipt) {
    const record: ReceiptRecord = { receipt, quote, occurredAt: receipt.paid_at };
    setReceipts((current) => [record, ...current.filter((entry) => entry.receipt.transaction_id !== receipt.transaction_id)].sort((a, b) => Date.parse(b.receipt.paid_at || b.occurredAt) - Date.parse(a.receipt.paid_at || a.occurredAt)));
  }

  async function openReceipt(record: ReceiptRecord) {
    const requestId = ++receiptDetailRequestId.current;
    setSelectedReceipt(record.receipt.id); setReceiptDetailError('');
    if (record.quote) { setReceiptDetailLoading(false); return; }
    setReceiptDetailLoading(true);
    try {
      const quote = await api.quoteById(TOKEN, record.receipt.quote_id);
      if (requestId === receiptDetailRequestId.current) setReceipts((current) => current.map((entry) => entry.receipt.id === record.receipt.id ? { ...entry, quote } : entry));
    } catch (err) {
      if (requestId === receiptDetailRequestId.current) setReceiptDetailError(err instanceof Error ? err.message : 'Item details are unavailable right now.');
    } finally { if (requestId === receiptDetailRequestId.current) setReceiptDetailLoading(false); }
  }

  useEffect(() => {
    if (!editingPreset && !receiptsOpen && !profileOpen) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    const focusables = dialog ? [...dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])')] : [];
    focusables[0]?.focus();
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const handleKeys = (event: KeyboardEvent) => {
      // Escape inside a store sign-in goes to the store's page, not to closing the sheet.
      if (event.key === 'Escape' && !(event.target instanceof HTMLElement && event.target.classList.contains('ob-typing'))) {
        setEditingPreset(null); setReceiptsOpen(false); setSelectedReceipt(null); setProfileOpen(false);
      }
      if (event.key === 'Tab' && focusables.length && !(event.target instanceof HTMLElement && event.target.classList.contains('ob-typing'))) {
        const first = focusables[0]; const last = focusables[focusables.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    window.addEventListener('keydown', handleKeys);
    return () => { window.removeEventListener('keydown', handleKeys); document.body.style.overflow = oldOverflow; previous?.focus(); };
  }, [editingPreset, receiptsOpen, selectedReceipt, profileOpen]);

  useEffect(() => {
    if (loaded && mandateId) void loadReceipts();
  }, [loaded, mandateId]);

  const kipState = !mandate || !active ? 'revoked' : card?.status === 'frozen' ? 'revoked' : phase === 'verdict' && verdict?.kind === 'paid' ? 'approved' : phase === 'verdict' && verdict?.kind === 'refused' ? 'refused' : 'idle';

  const estimate = (p: Pick) => p.items.reduce((sum, line) => sum + (productById.get(line.product_id)?.unit_price_minor ?? 0) * line.quantity, 0);
  const per = periodWord(mandate?.policy.period_limits[0]?.period);
  const blocked = new Set<string>(mandate?.policy.blocked_categories ?? []);
  const owner = possessive(holder);
  const expires = mandate ? new Date(mandate.policy.expires_at).toLocaleDateString('en-HK', { day: 'numeric', month: 'short' }) : '';
  const stamp = verdict?.kind === 'paid' ? 'Paid' : verdict?.kind === 'refused' ? 'Refused' : verdict?.kind === 'review' ? 'Your call' : 'Checking';

  return <div className={`m2 conversation-app${docked ? ' docked' : ''}`}>
    <header className="m2-top">
      <div className="m2-brand">Mandate</div>
      <div className="m2-top-right">
        <span className={`m2-pill ${online === false ? 'off' : ''}`}><i />{online === false ? 'Wallet offline' : 'Sandbox, no real money'}</span>
        {loaded && mandate && <button className="m2-profile-trigger" onClick={() => setProfileOpen(true)} aria-label="Profile: stores and allowance"><i><UserRound size={15} /></i><span>Profile</span></button>}
      </div>
    </header>

    {error && <div className="m2-error" role="alert"><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss">Dismiss</button></div>}

    {!loaded ? <div className="m2-loading"><Avatar name="kip" state="idle" size={96} /></div> : showSetup ? (
      <section className="m2-setup conversation-setup">
        <div className="conversation-intro"><p className="m2-kicker">YOUR SHOPPING COMPANION</p><h1>What can I find<br /><em>for you?</em></h1><p>Tell Kumi what you need. Kip keeps every purchase within the rules you confirm.</p></div>
        <MessageList messages={[{ id: 'setup-welcome', speaker: 'kumi', text: 'Hi! Before I shop, let’s agree on who I’m shopping for and what I may spend. Review the setup cards below—nothing is active until you confirm.' }]} />
        <Onboarding token={TOKEN} online={online} initialRiskReview={riskReviewOn} initialPolicy={setupOpen && mandate ? mandate.policy : null} initialHolder={setupOpen && mandate ? holder : ''} onActivated={activated}
          onBack={setupOpen && mandate ? () => setSetupOpen(false) : undefined} />
      </section>
    ) : mandate && (
      <main className={`m2-grid${phase === 'verdict' ? ' verdict' : ''}`}>
        <section className="m2-col m2-area-card">
          <div className={`m2-card ${canSpend ? '' : 'frozen'}`}>
            <div className="m2-card-top"><span>{owner} grocery card</span><span className="m2-status">{!active ? mandate.status === 'expired' ? 'Expired' : 'Revoked' : card?.status === 'frozen' ? 'Paused' : 'Active'}</span></div>
            {card && <div className="m2-card-number"><span>CONTROL CARD</span><b>{card.network === 'mastercard' ? 'Mastercard' : 'Visa'} ···· {card.last4}</b><small>the agent never sees reusable card details</small></div>}
            {active && !card && <p className="m2-card-note">Card details are unavailable for this allowance. Wallet spending rules remain active.</p>}
            <div className="m2-card-amount"><small>{!active ? 'Spending is off' : card?.status === 'frozen' ? 'Available when resumed' : `Left this ${per}`}</small><b>{active ? hkd(available) : 'HK$ 0'}</b></div>
            <div className="m2-meter" aria-hidden>{Array.from({ length: 20 }, (_, i) => <i key={i} className={i < Math.round(ratio * 20) ? 'on' : ''} />)}</div>
            <div className="m2-card-foot"><span>{hkd(spent)} used</span><span>{hkd(limit)} a {per}</span></div>
            <div className="m2-card-sticker"><Sticker name="kip" state={kipState} size={104} tilt={8} /></div>
          </div>
          <dl className="m2-rules">
            <div><dt>Most per order</dt><dd>{hkd(mandate.policy.per_order_limit_minor)}</dd></div>
            <div><dt>{mandate.policy.allowed_merchant_ids.length > 1 ? 'Shops' : 'Shop'}</dt><dd>{mandate.policy.allowed_merchant_ids.map(storeName).join(', ')}</dd></div>
            <div><dt>Never buy</dt><dd>{mandate.policy.blocked_categories.map(categoryLabel).join(', ') || 'Nothing blocked'}</dd></div>
            {mandate.policy.approval_above_minor != null && <div><dt>Ask me first above</dt><dd>{hkd(mandate.policy.approval_above_minor)}</dd></div>}
            {mandate.policy.risk_review && <div><dt>Extra protection</dt><dd>Unusual purchases reviewed</dd></div>}
            <div><dt>Ends</dt><dd>{expires}</dd></div>
          </dl>
          {active ? card?.status === 'frozen'
            ? <button className="m2-cta" onClick={() => void unfreeze()} disabled={busy === 'unfreeze'}><Snowflake size={18} />{busy === 'unfreeze' ? 'Resuming…' : 'Unfreeze the card'}</button>
            : <button className="m2-freeze" onClick={() => void freeze()} disabled={!card || busy === 'freeze'}><Snowflake size={18} />{busy === 'freeze' ? 'Freezing…' : card ? 'Freeze the card' : 'Card controls unavailable'}</button>
            : <button className="m2-cta" onClick={() => { setRiskReviewOn(Boolean(mandate.policy.risk_review)); setSetupOpen(true); }}>Start a new allowance<ArrowRight size={18} /></button>}
          {active && <button className="m2-link m2-revoke-link" onClick={() => void revokeAllowance()} disabled={busy === 'revoke'}>Permanently revoke allowance</button>}
          <button className="m2-receipts-trigger" onClick={showReceipts}><ReceiptText size={18} /><span><b>Receipts</b><small>{receipts.length ? `${receipts.length} purchases` : 'View past purchases'}</small></span><ArrowRight size={17} /></button>
          <a className="m2-receipts-trigger" href="?card"><CreditCard size={18} /><span><b>Virtual card</b><small>Set up, freeze or check the card</small></span><ArrowRight size={17} /></a>
          {receipts[0] && <button className="m2-latest-receipt" onClick={() => viewReceipt(receipts[0])}><span><small>LATEST RECEIPT</small><b>{merchantName(receipts[0].receipt.merchant_id)} · {new Date(receipts[0].receipt.paid_at || receipts[0].occurredAt).toLocaleDateString('en-HK', { day: 'numeric', month: 'short' })}</b></span><strong>{money(receipts[0].receipt.amount_minor)}</strong></button>}
        </section>

        <section className="m2-col m2-area-feed">
          <div className="m2-feed">
            <div className="m2-feed-head"><Sticker name="stella" state={feed.length ? 'pass' : 'idle'} size={46} tilt={-8} /><div><strong>Stella’s log</strong><small>{serverLog && serverLog.length ? 'From the wallet’s audit trail' : 'This session'}</small></div></div>
            {feed.length ? <ol>{feed.map((entry) => <li key={entry.id} className={entry.tone}><time>{new Date(entry.at).toLocaleTimeString('en-HK', { hour: '2-digit', minute: '2-digit', hour12: false })}</time><span className={`m2-dot ${entry.who}`} /><span>{entry.text}</span></li>)}</ol>
              : <p className="m2-empty">Nothing yet. Send Kumi shopping.</p>}
          </div>
        </section>

        <section className="m2-col m2-area-shop conversation-thread" ref={shopRef}>
          <div className="conversation-heading"><p className="m2-kicker">YOUR SHOPPING COMPANION</p><h1>What can I find for you?</h1><p>Shop at your allowed stores. Your confirmed spending rules stay in control.</p></div>
          <MessageList messages={[{ id: 'welcome', speaker: 'kumi', text: `Hi${holder ? `, shopping for ${holder}` : ''}! Tell me what you need. I can search your allowed stores and build a basket for you to review.` }, ...messages]} />
          <div className="conversation-active-card" aria-label="Current shopping step">

          {!canSpend ? (
            <div className="m2-panel m2-center">
              <Sticker name="kip" state="revoked" size={150} tilt={-4} />
              <h2>{!active ? 'Kip is asleep.' : 'Kip is taking a break.'}</h2>
              <p className="m2-muted">{!active ? `The allowance is ${mandate.status === 'expired' ? 'expired' : 'revoked'}. Nobody can spend from it, not even Kumi.` : 'This virtual card is frozen. Kumi cannot shop until you unfreeze it.'}</p>
              {active && <button className="m2-cta" onClick={() => void unfreeze()} disabled={busy === 'unfreeze'}>Unfreeze the card<ArrowRight size={18} /></button>}
            </div>
          ) : phase === 'pick' ? (
            <div className="m2-panel">
              <div className="m2-dock">
              <div className="m2-panel-head"><div><p className="m2-kicker">Kumi’s turn</p><h2>{holder ? `What does ${holder} need?` : `What would you like to buy?`}</h2></div><Sticker name="kumi" state="idle" size={84} tilt={6} /></div>
              <div className="m2-ask">
                <textarea value={listText} onChange={(e) => setListText(e.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && !voice) { event.preventDefault(); void shopFromText(); } }} rows={2} placeholder="Tell Kumi what’s needed, e.g. “rice, two litres of milk, 3 apples”" aria-label="Shopping list for Kumi" disabled={busy === 'parse' || voice === 'transcribing'} />
                <div className="m2-ask-actions">
                  <button type="button" className={`m2-mic${voice === 'recording' ? ' on' : ''}`} onClick={() => void toggleRecording()} disabled={voice === 'transcribing' || busy === 'parse'} aria-label={voice === 'recording' ? 'Stop recording' : 'Speak the list'}>
                    {voice === 'recording' ? <Square size={16} /> : <Mic size={16} />}{voice === 'recording' ? 'Stop' : voice === 'transcribing' ? 'Transcribing…' : 'Speak'}
                  </button>
                  <button type="button" className="m2-cta" onClick={() => void shopFromText()} disabled={!listText.trim() || busy === 'parse' || Boolean(voice) || !products.length}>{busy === 'parse' ? 'Reading your list…' : 'Send request'}<ArrowRight size={18} /></button>
                </div>
              </div>
              </div>
              <p className="m2-muted m2-or">Try a request</p>
              <div className="m2-picks">
                {visiblePicks.map((p) => { const Icon = p.icon; return <div key={p.id} className="m2-pick-row">
                  <button className={`m2-pick ${p.tone}`} onClick={() => void shop(p)} disabled={!products.length}>
                    <span className="m2-tile"><Icon size={24} strokeWidth={2.2} /></span>
                    <span className="m2-pick-text"><b>{p.title}</b><small>{p.subtitle}</small></span>
                    <span className="m2-pick-price">{products.length && !p.edited ? `~${hkd(estimate(p))}` : p.edited ? 'Personal' : ''}</span>
                  </button>
                  <button type="button" className="m2-edit-pick" onClick={() => editPreset(p)} aria-label={`Edit ${p.title}`} title={`Edit ${p.title}`}><Pencil size={16} /></button>
                </div>; })}
              </div>
              <button className="m2-link" onClick={() => setShowCustom((v) => !v)}>{showCustom ? 'Hide the shelf' : 'Or pick items yourself'}</button>
              {showCustom && <div className="m2-shelf">
                <input className="m2-search" placeholder="Search Wellcome" value={search} onChange={(e) => setSearch(e.target.value)} />
                <ul>{filtered.map((p: Product) => <li key={p.id}>
                  <span className="m2-shelf-title">{p.title}{blocked.has(p.category) && <em> {categoryLabel(p.category).toLowerCase()}</em>}</span>
                  <b>{hkd(p.unit_price_minor)}</b>
                  <span className="m2-qty">
                    {(custom[p.id] ?? 0) > 0 && <><button onClick={() => setCustom((c) => ({ ...c, [p.id]: Math.max(0, (c[p.id] ?? 0) - 1) }))} aria-label={`Remove one ${p.title}`}>−</button><i>{custom[p.id]}</i></>}
                    <button onClick={() => setCustom((c) => ({ ...c, [p.id]: Math.min(20, (c[p.id] ?? 0) + 1) }))} aria-label={`Add one ${p.title}`}>+</button>
                  </span>
                </li>)}</ul>
                <button className="m2-cta" disabled={!customCount} onClick={() => void shop('custom')}>{customCount ? `Price ${customCount} item${customCount > 1 ? 's' : ''}, about ${hkd(customTotal)}` : 'Add something first'}</button>
              </div>}
            </div>
          ) : phase === 'packing' ? (
            <div className="m2-panel m2-center">
              <Sticker name="kumi" state="idle" size={150} tilt={-5} />
              <h2>Kumi is packing{pick ? ` ${pick.title.toLowerCase()}` : ''}…</h2>
              <p className="m2-muted">Pricing it against {mandate.policy.allowed_merchant_ids.length > 1 ? 'each allowed store' : `${storeName(mandate.policy.allowed_merchant_ids[0])}’s shelf`}.</p>
              <div className="m2-dots"><i /><i /><i /></div>
            </div>
          ) : quote && (phase === 'basket' || phase === 'paying' || phase === 'verdict') ? (
            <div className="m2-panel m2-checkout">
              <div className="m2-panel-head">
                <div><p className="m2-kicker">{phase === 'verdict' ? 'Kip’s decision' : agentNote}</p><h2>{phase === 'verdict' && verdict ? verdictTitle(verdict, quote.total_minor) : phase === 'paying' ? 'Kip is checking the rules…' : 'Basket’s ready.'}</h2></div>
                <Sticker name={phase === 'verdict' && verdict?.kind === 'review' ? 'bean' : phase === 'verdict' ? 'kip' : 'kumi'} state={phase === 'verdict' && verdict ? (verdict.kind === 'paid' ? 'approved' : verdict.kind === 'refused' ? 'refused' : 'idle') : phase === 'paying' ? 'idle' : 'happy'} size={phase === 'verdict' ? 112 : 84} tilt={phase === 'verdict' ? -7 : 6} />
              </div>
              {phase === 'verdict' && verdict?.kind === 'paid' && <p className="m2-muted">The sandbox payment completed within the wallet rules. This is not a retailer order confirmation.</p>}
              {phase === 'verdict' && verdict?.kind === 'refused' && <p className="m2-muted">Nothing was paid.</p>}
              <div className={`m2-receipt${phase === 'verdict' && verdict ? ` stamped is-${verdict.kind}` : ''}`}>
                <div className="m2-receipt-head"><span>{storeName(quote.merchant_id).toUpperCase()} · CLICK &amp; COLLECT</span><span>{new Date(quote.created_at).toLocaleDateString('en-HK', { day: '2-digit', month: 'short' })}</span></div>
                <ul>{quote.items.map((item) => <li key={item.product_id} className={blocked.has(catalogById.get(item.product_id)?.category ?? '') ? 'flag' : ''}><span>{item.quantity}×</span><span data-flag={categoryLabel(catalogById.get(item.product_id)?.category ?? '').toLowerCase()}>{item.title}</span><b>{money(item.line_total_minor)}</b></li>)}
                  {quote.charges.map((charge, i) => <li key={`c${i}`} className="m2-charge"><span /><span>{charge.label}</span><b>{charge.amount_minor ? money(charge.amount_minor) : 'FREE'}</b></li>)}
                </ul>
                <div className="m2-receipt-total"><span>TOTAL</span><b>{money(quote.total_minor)}</b></div>
                {phase === 'verdict' && verdict?.kind === 'paid' && <div className="m2-receipt-meta"><span>Receipt</span><span>{verdict.receipt.id.slice(0, 18)}…</span>{verdict.receipt.payment_route && <><span>Paid via</span><span>{verdict.receipt.payment_route.label}</span></>}<span>Left this {per}</span><span>{money(available)}</span></div>}
                {phase === 'verdict' && verdict && <div className={`m2-stamp ${verdict.kind}`}>{stamp}</div>}
              </div>
              {phase === 'basket' && <>
                <CartSyncPanel key={quote.id} token={TOKEN} mandateId={mandate.id} quote={quote} />
                <button className="m2-cta" onClick={() => void checkout()}>Confirm sandbox payment · {hkd(quote.total_minor)}<ArrowRight size={18} /></button>
                {routeLabel && <p className="m2-route">Kip will use <b>{routeLabel}</b>, the cheapest route it found.</p>}
                <button className="m2-link" onClick={resetShop}>Start over</button>
              </>}
              {phase === 'paying' && <div className="m2-dots center"><i /><i /><i /></div>}
              {phase === 'verdict' && verdict?.kind === 'refused' && <>
                {verdict.violations.length > 0 ? <ul className="m2-why">{verdict.violations.map((v, i) => <li key={`${v.rule_id}-${i}`}><b>{REASONS[v.code] ?? v.code.replace(/_/g, ' ').toLowerCase()}</b><small>{v.message}</small></li>)}</ul> : <p className="m2-muted">{verdict.message}</p>}
              </>}
              {phase === 'verdict' && verdict?.kind === 'uncertain' && <><p className="m2-muted">{verdict.message}</p><button className="m2-cta" onClick={() => void checkout()}>Check again</button></>}
              {phase === 'verdict' && verdict?.kind === 'review' && <>
                <p className="m2-muted">Kip paused this order for your review. Nothing is reserved or paid while it waits. Check each reason before deciding:</p>
                {verdict.violations.length > 0 && <ul className="m2-why m2-review-reasons">{verdict.violations.map((v, i) => <li key={`${v.rule_id}-${i}`}><b>{REASONS[v.code] ?? v.code.replace(/_/g, ' ').toLowerCase()}</b><small>{v.message}</small></li>)}</ul>}
                <p className="m2-muted">Approval covers only this basket and the reasons shown here, once.</p>
                <div className="m2-row">
                  <button className="m2-cta" onClick={() => void decide(true)} disabled={busy === 'decide'}>Approve once</button>
                  <button className="m2-ghost" onClick={() => void decide(false)} disabled={busy === 'decide'}>Say no</button>
                </div>
              </>}
              {phase === 'verdict' && verdict?.kind !== 'review' && <button className="m2-ghost" onClick={resetShop}>Shop again</button>}
            </div>
          ) : null}
          </div>
        </section>
      </main>
    )}

    {editingPreset && <div className="m2-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditingPreset(null); }}>
      <section className="m2-modal m2-preset-modal" role="dialog" aria-modal="true" aria-labelledby="preset-editor-title">
        <header className="m2-modal-head"><div><p className="m2-kicker">Make it yours</p><h2 id="preset-editor-title">Edit shopping preset</h2></div><button className="m2-icon-button" onClick={() => setEditingPreset(null)} aria-label="Close editor"><X size={20} /></button></header>
        <label className="m2-field">Preset name<input value={editorTitle} maxLength={60} onChange={(event) => setEditorTitle(event.target.value)} /></label>
        <div className="m2-editor-items-head"><b>Shopping list</b><small>Saved on this device</small></div>
        <div className="m2-editor-items">{editorItems.map((item, index) => <div className="m2-editor-item" key={index}>
          <input aria-label={`Item ${index + 1}`} value={item.name} maxLength={100} placeholder="e.g. jasmine rice" onChange={(event) => setEditorItems((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, name: event.target.value } : row))} />
          <label><span>Qty</span><input aria-label={`Quantity for item ${index + 1}`} type="number" min="1" max="20" value={item.quantity || ''} onChange={(event) => setEditorItems((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, quantity: event.target.value === '' ? 0 : Number(event.target.value) } : row))} /></label>
          <input aria-label={`Unit for item ${index + 1} (optional)`} value={item.unit ?? ''} maxLength={24} placeholder="Unit" onChange={(event) => setEditorItems((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, unit: event.target.value } : row))} />
          <button className="m2-icon-button danger" onClick={() => setEditorItems((current) => current.filter((_, rowIndex) => rowIndex !== index))} aria-label={`Remove ${item.name || `item ${index + 1}`}`}><Trash2 size={17} /></button>
        </div>)}</div>
        <button className="m2-add-item" onClick={() => { if (editorItems.length < 20) setEditorItems((current) => [...current, { name: '', quantity: 1 }]); }} disabled={editorItems.length >= 20}>+ Add an item</button>
        {presetError && <p className="m2-modal-error" role="alert">{presetError}</p>}
        <div className="m2-modal-actions">
          <button className="m2-ghost" onClick={() => resetPreset(editingPreset.id)}>{PICKS.some((item) => item.id === editingPreset.id) ? <><RotateCcw size={16} />Reset default</> : <><Trash2 size={16} />Delete copy</>}</button>
          <button className="m2-ghost" onClick={duplicatePreset}>Make a copy</button>
          <button className="m2-cta" onClick={savePreset}>Save preset</button>
        </div>
        <p className="m2-modal-note">When you shop, Kumi receives this edited list. If Kumi is unavailable, the original preset will not be substituted.</p>
      </section>
    </div>}

    {profileOpen && <ProfileSheet token={TOKEN} online={online} mandate={mandate} holder={holder} onClose={() => setProfileOpen(false)}
      onChangeRules={() => { setProfileOpen(false); setRiskReviewOn(Boolean(mandate?.policy.risk_review)); setSetupOpen(true); }} />}
    {receiptsOpen && <div className="m2-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setReceiptsOpen(false); }}>
      <section className="m2-modal m2-receipts-modal" role="dialog" aria-modal="true" aria-labelledby="receipts-title">
        <header className="m2-modal-head"><div><p className="m2-kicker">Your purchase history</p><h2 id="receipts-title">{selectedReceiptRecord ? 'Receipt' : 'Receipts'}</h2></div><button className="m2-icon-button" onClick={() => { setReceiptsOpen(false); setSelectedReceipt(null); }} aria-label="Close receipts"><X size={20} /></button></header>
        {selectedReceiptRecord ? <>
          <button className="m2-back-link" onClick={() => setSelectedReceipt(null)}>← All receipts</button>
          <article className="m2-paper-receipt" id="printable-receipt">
            <div className="m2-paper-brand">{merchantName(selectedReceiptRecord.receipt.merchant_id).toUpperCase()} <span>MANDATE · SANDBOX</span></div>
            <p className="m2-paper-date">{new Date(selectedReceiptRecord.receipt.paid_at || selectedReceiptRecord.occurredAt).toLocaleString('en-HK', { dateStyle: 'long', timeStyle: 'short' })}</p>
            {receiptDetailLoading ? <p className="m2-muted">Loading item details…</p> : selectedReceiptRecord.quote ? <ul>{selectedReceiptRecord.quote.items.map((item) => <li key={item.product_id}><span>{item.quantity}× {item.title}</span><b>{money(item.line_total_minor)}</b></li>)}{selectedReceiptRecord.quote.charges.map((charge, index) => <li key={`${charge.label}-${index}`}><span>{charge.label}</span><b>{charge.amount_minor ? money(charge.amount_minor) : 'FREE'}</b></li>)}</ul> : <p className="m2-muted">{receiptDetailError || 'Item details are no longer available for this purchase.'}{receiptDetailError && <button className="m2-back-link" onClick={() => void openReceipt(selectedReceiptRecord)}>Try again</button>}</p>}
            <div className="m2-paper-total"><span>Paid</span><b>{money(selectedReceiptRecord.receipt.amount_minor)}</b></div>
            <dl><dt>Payment route</dt><dd>{selectedReceiptRecord.receipt.payment_route?.label ?? 'Sandbox wallet'}</dd><dt>Receipt ID</dt><dd>{selectedReceiptRecord.receipt.id}</dd><dt>Transaction</dt><dd>{selectedReceiptRecord.receipt.transaction_id}</dd></dl>
          </article>
          <button className="m2-cta m2-print-button" onClick={() => window.print()}><Printer size={17} />Print / Save PDF</button>
        </> : <>
          {receiptsLoading ? <div className="m2-receipt-loading"><Avatar name="stella" state="idle" size={64} /><p>Finding your receipts…</p></div> : receiptsError ? <div className="m2-receipts-state"><p role="alert">{receiptsError}</p><button className="m2-ghost" onClick={() => void loadReceipts(true)}>Try again</button></div> : receipts.length ? <div className="m2-receipt-list">{receipts.map((record) => <button className="m2-receipt-row" key={record.receipt.transaction_id} onClick={() => void openReceipt(record)}>
            <span className="m2-receipt-icon"><ReceiptText size={19} /></span><span className="m2-receipt-row-text"><b>{merchantName(record.receipt.merchant_id)} groceries</b><small>{new Date(record.receipt.paid_at || record.occurredAt).toLocaleString('en-HK', { dateStyle: 'medium', timeStyle: 'short' })}</small></span><b className="m2-receipt-row-amount">{money(record.receipt.amount_minor)}</b><ArrowRight size={17} />
          </button>)}</div> : <div className="m2-receipts-state"><Sticker name="stella" state="idle" size={104} tilt={-5} /><h3>No paid receipts yet</h3><p>Completed sandbox purchases will appear here. Refused orders are not receipts.</p></div>}
          {!receiptsLoading && receipts.length > 0 && <button className="m2-refresh-receipts" onClick={() => void loadReceipts(true)}>Refresh history</button>}
        </>}
      </section>
    </div>}

    <footer className="m2-foot">
      <button className="m2-link" onClick={() => setShowFine((v) => !v)}>How this works</button>
      {showFine && <div className="m2-fine-print">
        <p><b>Kip has the final say.</b> Kumi, the shopping agent, can only suggest a basket. Kip is a separate wallet service. It checks every purchase against your rules, then signs it or refuses it.</p>
        <p><b>Prices</b> come from a Wellcome snapshot{snapshotAt ? ` taken ${snapshotAt}` : ''}. Click &amp; Collect only, free above HK$50. Offers may have changed.</p>
        <p><b>Payments</b> run in a sandbox. No real money moves.</p>
        <a href="?classic">Open the full dashboard (evidence, audit, safety lab)</a>
      </div>}
    </footer>
  </div>;
}

function verdictTitle(verdict: Verdict, total: number) {
  if (verdict.kind === 'paid') return `Paid. ${hkd(verdict.receipt.amount_minor)}.`;
  if (verdict.kind === 'refused') return 'Nope. Kip said no.';
  if (verdict.kind === 'review') return `Kumi wants to spend ${hkd(total)}.`;
  return 'Still checking.';
}
