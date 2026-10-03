import { useEffect, useMemo, useRef, useState } from 'react';
import type { LucideIcon } from 'lucide-react';
import { Ban, Bot, CircleAlert, Crown, FlaskConical, KeyRound, Loader2, Network, Play, Radio, Receipt, Repeat, RotateCcw, ShieldCheck, ShieldX, ShoppingBasket, Shuffle, Store, Tag, Timer, Users, Volume2, VolumeX, Wine, Zap } from 'lucide-react';
import type { AttackResult, AttackSummary, AttackStep } from '../../../../contracts/types';
import { ApiError, api } from '../lib/api';
import { money } from '../lib/format';
import HarnessStage from './HarnessStage';
import LiveMonitor, { type LiveSummary } from './LiveMonitor';
import { packetForStep, type Packet } from './gates';
import { onSoundChange, play, setSound, soundOn } from './sound';
import '../simple/openai-tokens.css';
import './security.css';

/*
 * Security lab for the agent harness. Every result on this page comes from the wallet: attack results from
 * POST /demo/attacks/{id}/runs (real wallet code against a fresh, isolated wallet) and live results from the user's
 * own audit stream. Nothing here decides whether a defence held; the page only displays what the wallet answered.
 */

const TOKEN = 'dev-user-token';

const ATTACK_ICONS: Record<string, LucideIcon> = {
  control: ShoppingBasket, 'over-order-cap': Receipt, 'blocked-category': Wine, 'blocked-merchant': Store,
  'price-injection': Tag, 'forged-token': KeyRound, 'quote-swap': Shuffle, 'double-spend': Zap, replay: Repeat,
  'revoked-mid-flight': Ban, 'expired-capability': Timer, 'privilege-escalation': Crown, 'cross-family': Users,
  'sibling-agent': Bot, 'agent-swarm': Network,
};

type RunState = { status: 'idle' } | { status: 'running' } | { status: 'done'; result: AttackResult } | { status: 'failed'; message: string };

function actorLabel(actor: string) {
  if (actor === 'user') return 'Owner';
  if (actor === 'agent') return 'Agent';
  if (actor === 'stranger') return 'Other household';
  if (actor === 'stranger_agent') return 'Their agent';
  if (actor.startsWith('swarm_agent_')) return `Agent ${actor.slice('swarm_agent_'.length)}`;
  return actor;
}

function outcomeTone(outcome: string) {
  if (outcome.startsWith('completed') || outcome === 'approved' || outcome === 'active') return 'ok';
  if (outcome.startsWith('refused') || outcome.startsWith('HTTP 4') || outcome.startsWith('requires_review')) return 'blocked';
  return 'neutral';
}

export default function SecurityLab() {
  const [attacks, setAttacks] = useState<AttackSummary[] | null>(null);
  const [loadError, setLoadError] = useState('');
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [runningAll, setRunningAll] = useState(false);
  const [tab, setTab] = useState<'live' | 'lab'>(() => new URLSearchParams(window.location.search).get('security') === 'lab' ? 'lab' : 'live');
  // Each tab has its own stage so live counters only ever count real wallet decisions.
  const [livePackets, setLivePackets] = useState<Packet[]>([]);
  const [labPackets, setLabPackets] = useState<Packet[]>([]);
  const [live, setLive] = useState<LiveSummary>({ decisions: 0, blocked: 0, held: 0, paidMinor: 0 });
  const stageRef = useRef<HTMLDivElement>(null);
  const pending = useRef(new Map<string, { id: string; result: AttackResult; remaining: number }>());
  function landed(packet: Packet) {
    const entry = packet.group ? pending.current.get(packet.group) : undefined;
    if (!entry || --entry.remaining > 0) return;
    pending.current.delete(packet.group!);
    setRuns((prev) => ({ ...prev, [entry.id]: { status: 'done', result: entry.result } }));
    if (fetchedAll.current && pending.current.size === 0) setRunningAll(false);
  }
  const fetchedAll = useRef(false);
  const [sound, setSoundState] = useState(soundOn);
  useEffect(() => onSoundChange(setSoundState), []);
  const append = (set: typeof setLivePackets) => (next: Packet[]) => { if (next.length) set((prev) => [...prev, ...next].slice(-200)); };

  useEffect(() => {
    api.attacks(TOKEN).then((list) => setAttacks(list.attacks)).catch((error: unknown) => {
      setLoadError(error instanceof ApiError && error.status === 403
        ? 'The attack lab is switched off. Start the API with MANDATE_ENABLE_DEMO_CHECKOUT=1 (just dev and just demo do this).'
        : error instanceof Error ? error.message : 'Could not reach the wallet API.');
    });
  }, []);

  async function run(id: string) {
    setRuns((prev) => ({ ...prev, [id]: { status: 'running' } }));
    try {
      const result = await api.runAttack(TOKEN, id);
      const racing = result.category === 'concurrency' || result.category === 'scale';
      const steps = result.steps.filter((step) => step.phase === 'attack');
      const group = `${result.id}|${result.ran_at}`;
      const packets = steps.map((step, i) => ({
        ...packetForStep(step, `${group}-${i}`, racing && i > 0 && step.path === '/authorizations' && steps[i - 1].path === '/authorizations'),
        group,
      }));
      // Show the result when the animation gets there, so tiles and scores never run ahead of the stage.
      if (packets.length) {
        pending.current.set(group, { id, result, remaining: packets.length });
        append(setLabPackets)(packets);
      } else {
        setRuns((prev) => ({ ...prev, [id]: { status: 'done', result } }));
      }
      return result;
    } catch (error) {
      setRuns((prev) => ({ ...prev, [id]: { status: 'failed', message: error instanceof Error ? error.message : 'Request failed' } }));
      return null;
    }
  }

  async function runAll() {
    if (!attacks || runningAll) return;
    setTab('lab');
    play('launch');
    stageRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    fetchedAll.current = false;
    setRunningAll(true);
    setRuns({});
    setSelected(null);
    for (const attack of attacks) await run(attack.id);
    // Stay busy until the stage has shown every result.
    fetchedAll.current = true;
    if (pending.current.size === 0) setRunningAll(false);
  }

  const results = useMemo(() => Object.values(runs).flatMap((state) => state.status === 'done' ? [state.result] : []), [runs]);
  const attackResults = results.filter((result) => result.category !== 'control');
  const blocked = attackResults.filter((result) => result.held).length;
  const attackTotal = attacks?.filter((attack) => attack.category !== 'control').length ?? 0;
  const control = results.find((result) => result.category === 'control');
  // Money that moved when it should not have: payments from attacks whose defence failed, plus any spend past a limit.
  const unauthorized = results.reduce((sum, result) => {
    const ledger = result.ledger;
    if (!ledger) return sum;
    const over = ledger.week_limit_minor !== null && ledger.week_paid_minor !== null ? Math.max(0, ledger.week_paid_minor - ledger.week_limit_minor) : 0;
    return sum + over + (result.category !== 'control' && !result.held ? ledger.paid_total_minor : 0);
  }, 0);
  const requests = results.reduce((sum, result) => sum + result.steps.filter((step) => step.phase === 'attack').length, 0);
  // The stage plays results in order, so the oldest pending run is the one on screen; the rest are queued.
  const onStage = pending.current.values().next().value?.id as string | undefined;
  const queued = new Set([...pending.current.values()].map((entry) => entry.id).filter((id) => id !== onStage));
  const current = selected ? attacks?.find((attack) => attack.id === selected) ?? null : null;

  return <div className="sl">
    <section className="sl-dark" data-theme="dark">
      <header className="sl-top">
        <span className="sl-brand">Mandate <span>Security lab</span></span>
        <nav><button className="sl-sound" onClick={() => setSound(!sound)} aria-pressed={sound} aria-label={sound ? 'Mute sound effects' : 'Turn on sound effects'}>{sound ? <Volume2 size={16} /> : <VolumeX size={16} />}{sound ? 'Sound on' : 'Sound off'}</button><a href="?about">About</a><a href="?classic">Audit &amp; verification</a><a className="sl-nav-cta" href="./">Open the demo</a></nav>
      </header>

      <div className="sl-hero">
        <p className="sl-kicker">An agent harness, not an agent</p>
        <h1>Bring any agent. <em>The harness holds.</em></h1>
        <p className="sl-lede">Agents can ask to buy. Only Mandate can price, reserve, sign and pay, and it checks every request at five gates.</p>
        <div className="sl-hero-actions">
          <button className="sl-launch" onClick={() => void runAll()} disabled={!attacks || runningAll}>
            {runningAll ? <><Loader2 size={18} className="sl-spin" />Attacking…</> : <><Zap size={18} />Launch {attackTotal || ''} attacks</>}
          </button>
          <button className="sl-ghost" onClick={() => { setTab('live'); stageRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}><Radio size={18} />Watch my wallet live</button>
        </div>
        <p className="sl-works">Works with Jev, Claude and ChatGPT through MCP, or any HTTP agent. A hijacked agent gets the same limits.</p>
      </div>

      <div ref={stageRef} className="sl-stage-wrap">
        <div hidden={tab !== 'live'}><HarnessStage packets={livePackets} audible={tab === 'live'} mode="LIVE · your wallet" status="Real decisions from the running wallet" /></div>
        <div hidden={tab !== 'lab'}><HarnessStage packets={labPackets} audible={tab === 'lab'} onLand={landed} mode="ATTACK LAB · throwaway wallet" status="Real wallet code, fresh sandbox per attack" /></div>
      </div>

      {tab === 'lab'
        ? <div className="sl-score" aria-live="polite">
          <div className={results.length ? (blocked === attackResults.length ? 'good' : 'bad') : ''}><small>Attacks blocked</small><strong>{blocked}<span>/{attackTotal || '—'}</span></strong></div>
          <div className={results.length ? (unauthorized ? 'bad' : 'good') : ''}><small>Money stolen</small><strong>{results.length ? money(unauthorized) : '—'}</strong></div>
          <div className={control ? (control.held ? 'good' : 'bad') : ''}><small>Real purchase still works</small><strong>{control ? (control.held ? 'Yes' : 'No') : '—'}</strong></div>
          <div><small>Attack requests fired</small><strong>{requests || '—'}</strong></div>
        </div>
        : <div className="sl-score" aria-live="polite">
          <div><small>Decisions since you opened this</small><strong>{live.decisions}</strong></div>
          <div className={live.blocked ? 'bad' : ''}><small>Blocked</small><strong>{live.blocked}</strong></div>
          <div className={live.held ? 'warn' : ''}><small>Waiting for the owner</small><strong>{live.held}</strong></div>
          <div className={live.paidMinor ? 'good' : ''}><small>Paid</small><strong>{money(live.paidMinor)}</strong></div>
        </div>}

      <div className="sl-tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'live'} className={tab === 'live' ? 'on' : ''} onClick={() => setTab('live')}><Radio size={16} />Live monitor</button>
        <button role="tab" aria-selected={tab === 'lab'} className={tab === 'lab' ? 'on' : ''} onClick={() => setTab('lab')}><FlaskConical size={16} />Attack lab</button>
      </div>
    </section>

    <section className="sl-board" hidden={tab !== 'live'}><LiveMonitor onPackets={append(setLivePackets)} onSummary={setLive} /></section>

    {tab === 'lab' && <section className="sl-board">
      <div className="sl-board-head">
        <div>
          <h2>{attackTotal} ways to try to break it</h2>
          <p>Each attack hits a brand-new throwaway wallet that is deleted afterwards. Your real wallet is never touched. Click any tile to see exactly what happened.</p>
        </div>
        <button className="sl-cta" onClick={() => void runAll()} disabled={!attacks || runningAll}>
          {runningAll ? <><Loader2 size={17} className="sl-spin" />Running…</> : results.length ? <><RotateCcw size={17} />Run all again</> : <><Play size={17} />Run all</>}
        </button>
      </div>

      {loadError && <div className="sl-alert"><CircleAlert size={18} />{loadError}</div>}
      {!attacks && !loadError && <div className="sl-loading"><Loader2 size={18} className="sl-spin" />Loading attacks…</div>}

      <div className="sl-tiles">
        {attacks?.map((attack) => <AttackTile key={attack.id} attack={attack} state={runs[attack.id] ?? { status: 'idle' }} queued={queued.has(attack.id)}
          selected={selected === attack.id} onSelect={() => setSelected(selected === attack.id ? null : attack.id)} />)}
      </div>

      {current && <AttackDetail attack={current} state={runs[current.id] ?? { status: 'idle' }} disabled={runningAll}
        onRun={() => { stageRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); void run(current.id); }} />}
    </section>}

    <footer className="sl-foot">
      <p>Sandbox prototype. No real money moves. The attack lab uses a placeholder catalog and a fixed clock (Wednesday, 7 October 2026, 10:00 HKT) so every run is reproducible. Passing these attacks covers these setups only and is not a proof that the wallet is safe. For the wider measurement against an unsafe baseline, see <code>evaluation/</code>; for the bounded Z3 model and audit verifier, see <a href="?classic">Activity &amp; safety</a>.</p>
    </footer>
  </div>;
}

function AttackTile({ attack, state, queued, selected, onSelect }: { attack: AttackSummary; state: RunState; queued: boolean; selected: boolean; onSelect: () => void }) {
  const Icon = ATTACK_ICONS[attack.id] ?? ShieldCheck;
  const isControl = attack.category === 'control';
  const result = state.status === 'done' ? state.result : null;
  const tone = state.status === 'running' && queued ? 'queued' : state.status === 'running' ? 'running' : state.status === 'failed' ? 'bad' : result ? (result.held ? 'good' : 'bad') : 'idle';
  const label = tone === 'queued' ? 'Queued' : state.status === 'running' ? 'Attacking…' : state.status === 'failed' ? 'Could not run'
    : result ? (result.held ? (isControl ? 'Allowed' : 'Blocked') : (isControl ? 'Wrongly refused' : 'Breached')) : isControl ? 'Normal purchase' : 'Ready';
  const StatusIcon = tone === 'running' ? Loader2 : tone === 'good' ? ShieldCheck : tone === 'bad' ? ShieldX : null;
  return <button className={`sl-tile ${tone} ${selected ? 'selected' : ''} ${isControl ? 'control' : ''}`} onClick={onSelect} aria-pressed={selected}>
    <span className="sl-tile-icon"><Icon size={20} /></span>
    <strong>{attack.title}</strong>
    <span className="sl-tile-status">{StatusIcon && <StatusIcon size={14} className={tone === 'running' ? 'sl-spin' : ''} />}{label}</span>
  </button>;
}

function AttackDetail({ attack, state, disabled, onRun }: { attack: AttackSummary; state: RunState; disabled: boolean; onRun: () => void }) {
  const result = state.status === 'done' ? state.result : null;
  return <article className="sl-detail">
    <div className="sl-detail-head">
      <div>
        <h3>{attack.title}</h3>
        <p><b>The attack.</b> {attack.threat}</p>
        <p className="sl-defence"><ShieldCheck size={15} /><span><b>Why it fails.</b> {attack.defence}</span></p>
      </div>
      <button className="sl-run" onClick={onRun} disabled={disabled || state.status === 'running'}>
        {state.status === 'running' ? <Loader2 size={14} className="sl-spin" /> : <Play size={14} />}{result ? 'Run again' : 'Run this attack'}
      </button>
    </div>
    {state.status === 'failed' && <div className="sl-alert"><CircleAlert size={16} />{state.message}</div>}
    {result && <Trace result={result} />}
    {state.status === 'idle' && <p className="sl-hint">Run it to see every request the attacker sends and what the wallet answers.</p>}
  </article>;
}

function Trace({ result }: { result: AttackResult }) {
  const [showSetup, setShowSetup] = useState(false);
  const setup = result.steps.filter((step) => step.phase === 'setup');
  const attack = result.steps.filter((step) => step.phase === 'attack');
  const ledger = result.ledger;
  return <div className="sl-trace">
    <div className="sl-verdict">
      <div><small>Expected</small><code>{result.expected}</code></div>
      <div><small>Wallet answered</small><code className={result.held ? 'good' : 'bad'}>{result.observed}</code></div>
      {ledger && <div><small>Ledger afterwards</small><span>{ledger.completed_payments} payment{ledger.completed_payments === 1 ? '' : 's'} · {money(ledger.paid_total_minor)} paid{ledger.week_limit_minor !== null && ledger.week_paid_minor !== null ? ` · ${money(ledger.week_paid_minor)} of ${money(ledger.week_limit_minor)} this week` : ''}</span></div>}
    </div>
    {result.error && <div className="sl-alert"><CircleAlert size={16} />{result.error}</div>}
    {setup.length > 0 && <button className="sl-setup-toggle" onClick={() => setShowSetup(!showSetup)}>{showSetup ? 'Hide' : 'Show'} {setup.length} setup request{setup.length === 1 ? '' : 's'}</button>}
    <ol className="sl-steps">
      {(showSetup ? setup : []).map((step, i) => <Step key={`s${i}`} step={step} />)}
      {attack.map((step, i) => <Step key={`a${i}`} step={step} />)}
    </ol>
  </div>;
}

function Step({ step }: { step: AttackStep }) {
  const [expanded, setExpanded] = useState(false);
  const tone = outcomeTone(step.outcome);
  const violation = (step.response.violations as { message?: string }[] | undefined)?.[0]?.message
    ?? (step.response.error as { message?: string } | undefined)?.message;
  return <li className={`sl-step ${step.phase}`}>
    <span className={`sl-actor ${step.actor.startsWith('swarm') ? 'swarm' : step.actor}`}>{actorLabel(step.actor)}</span>
    <div className="sl-step-main">
      <button className="sl-step-line" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
        <span className="sl-step-title">{step.title}</span>
        <code className="sl-endpoint">{step.method} {step.path}</code>
      </button>
      {violation && tone === 'blocked' && <small className="sl-reason">{violation}</small>}
      {expanded && <pre className="sl-json">{JSON.stringify({ request: step.request, http_status: step.http_status, response: step.response }, null, 2)}</pre>}
    </div>
    <span className={`sl-outcome ${tone}`}>{step.outcome}</span>
  </li>;
}
