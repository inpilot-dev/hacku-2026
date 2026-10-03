import { useEffect, useRef, useState } from 'react';
import { BadgeCheck, Bot, Check, CircleAlert, CreditCard, Fingerprint, Info, LockKeyhole, ScrollText, Tag, X } from 'lucide-react';
import { GATES, RAIL, type Packet, type PacketResult } from './gates';
import { play } from './sound';

/*
 * Animated view of the harness. Packets come from wallet answers (lab traces or live audit events); the stage only
 * draws where each one stopped. Positions are in a 1000 × 300 viewBox.
 */

const X0 = 74;
const GATE_X = GATES.map((_, i) => 215 + i * 135);
const RAIL_X = 912;
const Y = 150;
const SPEED = 0.32; // viewBox units per ms: about 2.6 s from the agent to the payment rail
const HOLD = 2400;  // how long a landed packet stays visible
const FADE = 600;
const GAP = 1000;   // spacing between consecutive requests
const BURST_GAP = 150; // spacing inside a race, so racing agents still leave together
const FLASH = 3200; // how long a gate stays lit and keeps its tag
const TAG_FADE = 700;
const ICONS = [Fingerprint, Tag, ScrollText, LockKeyhole, BadgeCheck];

const KIND_COLOR = { agent: 'var(--color-text-info)', swarm: 'var(--color-text-discovery)', owner: 'var(--color-text-secondary)', stranger: 'var(--color-text-caution)' };
const RESULT_COLOR: Record<PacketResult, string> = { pass: 'var(--color-text-success)', block: 'var(--color-text-danger)', review: 'var(--color-text-warning)', info: 'var(--color-text-info)' };

type Live = Packet & { launchAt: number; lane: number; counted: boolean };
type Flash = { result: PacketResult; tag: string; at: number };
type Counts = { block: number; pass: number };
type Note = { id: string; text: string; verdict: string; result: PacketResult };

function verdictFor(p: Packet) {
  const where = p.stopAt >= RAIL ? 'Payment rail' : GATES[p.stopAt].name;
  if (p.result === 'block') return `Stopped at ${where}: ${p.tag}`;
  if (p.result === 'review') return `Held at ${where}: ${p.tag}`;
  if (p.stopAt >= RAIL) return p.tag;
  return p.result === 'pass' ? `${where} ✓ ${p.tag}` : p.tag;
}
const NOTE_ICON = { pass: Check, block: X, review: CircleAlert, info: Info };

function stopX(packet: Packet) {
  if (packet.stopAt >= RAIL) return RAIL_X - 34;
  const gx = GATE_X[packet.stopAt];
  return packet.result === 'block' || packet.result === 'review' ? gx - 46 : gx;
}

const ease = (t: number) => 1 - Math.pow(1 - t, 3);

export default function HarnessStage({ packets, mode, status, audible = true, onLand }: { packets: Packet[]; mode: string; status: string; audible?: boolean; onLand?: (packet: Packet) => void }) {
  const live = useRef<Live[]>([]);
  const seen = useRef(new Set<string>());
  const lastLaunch = useRef(0);
  const burstLane = useRef(0);
  const flashes = useRef<Record<number, Flash>>({});
  const counts = useRef<Counts[]>(Array.from({ length: RAIL + 1 }, () => ({ block: 0, pass: 0 })));
  const notes = useRef<Note[]>([]);
  const audibleRef = useRef(audible);
  audibleRef.current = audible;
  const onLandRef = useRef(onLand);
  onLandRef.current = onLand;
  const [now, setNow] = useState(() => performance.now());
  const frame = useRef<number | null>(null);
  const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  useEffect(() => {
    const t = performance.now();
    let added = false;
    for (const packet of packets) {
      if (seen.current.has(packet.id)) continue;
      seen.current.add(packet.id);
      const launchAt = packet.burst ? Math.max(t, lastLaunch.current + BURST_GAP) : Math.max(t, lastLaunch.current + GAP);
      burstLane.current = packet.burst ? burstLane.current + 1 : 0;
      lastLaunch.current = launchAt;
      live.current.push({ ...packet, launchAt, lane: burstLane.current, counted: false });
      added = true;
    }
    if ((added || live.current.length) && frame.current === null) tick();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [packets]);

  useEffect(() => () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
  }, []);

  function tick() {
    frame.current = requestAnimationFrame(() => {
      const t = performance.now();
      live.current = live.current.filter((p) => {
        const travel = (stopX(p) - X0) / SPEED;
        const elapsed = t - p.launchAt;
        if (elapsed >= travel && !p.counted) {
          p.counted = true;
          flashes.current[p.stopAt] = { result: p.result, tag: p.tag, at: t };
          if (p.result === 'block' || p.result === 'review') counts.current[p.stopAt].block += 1;
          if (p.result === 'pass') counts.current[p.stopAt].pass += 1;
          if (audibleRef.current) {
            play(p.result === 'block' ? 'block' : p.result === 'review' ? 'review' : p.stopAt >= RAIL && p.result === 'pass' ? 'paid' : 'pass', p.stopAt);
          }
          onLandRef.current?.(p);
          notes.current = [{ id: p.id, text: p.note, verdict: verdictFor(p), result: p.result }, ...notes.current].slice(0, 4);
        }
        return elapsed < travel + HOLD + FADE;
      });
      setNow(t);
      const flashing = Object.values(flashes.current).some((f) => t - f.at < FLASH);
      frame.current = null;
      if (live.current.length || flashing) tick();
    });
  }

  const laneY = (lane: number) => Y + (lane === 0 ? 0 : (lane % 2 ? -1 : 1) * Math.ceil(lane / 2) * 15);
  const flashFor = (i: number) => {
    const f = flashes.current[i];
    return f && now - f.at < FLASH ? f : null;
  };

  return <div className="st">
    <div className="st-head">
      <span className="st-mode"><i />{mode}</span>
      <span className="st-status">{status}</span>
    </div>
    <div className="st-scroll" role="img" aria-label={`Harness pipeline, ${mode}. ${status}`}>
      <svg viewBox="0 0 1000 300" className="st-svg">
        <defs>
          <pattern id="st-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" className="st-grid-line" /></pattern>
          <filter id="st-glow" x="-200%" y="-200%" width="500%" height="500%"><feGaussianBlur stdDeviation="4" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        <rect width="1000" height="300" fill="url(#st-grid)" />
        <line x1={X0} x2={RAIL_X} y1={Y} y2={Y} className="st-track" />

        {/* Source */}
        <g transform={`translate(${X0} ${Y})`}>
          <circle r="30" className="st-source" />
          <Bot x={-13} y={-13} width={26} height={26} color="var(--color-text-info)" />
          <text y="56" className="st-gate-name">Any agent</text>
          <text y="72" className="st-gate-detail">scoped token only</text>
        </g>

        {/* Gates */}
        {GATES.map((gate, i) => {
          const Icon = ICONS[i];
          const flash = flashFor(i);
          const color = flash ? RESULT_COLOR[flash.result] : 'var(--color-border-strong)';
          const c = counts.current[i];
          return <g key={gate.id} transform={`translate(${GATE_X[i]} ${Y})`}>
            <rect x="-30" y="-62" width="60" height="124" rx="14" className="st-gate" style={{ stroke: color }} filter={flash ? 'url(#st-glow)' : undefined} />
            <rect x="-30" y="-62" width="60" height="124" rx="14" className="st-gate-fill" />
            <Icon x={-11} y={-11} width={22} height={22} color={flash ? color : 'var(--color-text-tertiary)'} />
            {flash && <g className="st-tag" transform={`translate(0 ${i % 2 ? -26 : 0})`} style={{ opacity: Math.max(0, 1 - Math.max(0, now - flash.at - (FLASH - TAG_FADE)) / TAG_FADE) }}>
              {i % 2 ? <line y1="-76" y2="-62" stroke={color} strokeWidth="1.5" transform="translate(0 26)" /> : null}
              <rect x={-(flash.tag.length * 3.6 + 10)} y="-98" width={flash.tag.length * 7.2 + 20} height="22" rx="11" fill={color} />
              <text y="-83" className="st-tag-text">{flash.tag}</text>
            </g>}
            <text y="86" className="st-gate-name">{gate.name}</text>
            <text y="102" className="st-gate-detail">{gate.detail}</text>
            <text y="124" className="st-count"><tspan fill="var(--color-text-danger)">✕ {c.block}</tspan><tspan dx="10" fill="var(--color-text-success)">✓ {c.pass}</tspan></text>
          </g>;
        })}

        {/* Rail */}
        {(() => {
          const flash = flashFor(RAIL);
          const color = flash ? RESULT_COLOR[flash.result] : 'var(--color-border-strong)';
          return <g transform={`translate(${RAIL_X} ${Y})`}>
            <rect x="-34" y="-40" width="68" height="80" rx="16" className="st-gate" style={{ stroke: color }} filter={flash ? 'url(#st-glow)' : undefined} />
            <rect x="-34" y="-40" width="68" height="80" rx="16" className="st-gate-fill" />
            <CreditCard x={-13} y={-13} width={26} height={26} color={flash ? color : 'var(--color-text-tertiary)'} />
            {flash && <g className="st-tag" style={{ opacity: Math.max(0, 1 - Math.max(0, now - flash.at - (FLASH - TAG_FADE)) / TAG_FADE) }}>
              <rect x={-(flash.tag.length * 3.6 + 10) - 20} y="-76" width={flash.tag.length * 7.2 + 20} height="22" rx="11" fill={color} />
              <text x="-20" y="-61" className="st-tag-text">{flash.tag}</text>
            </g>}
            <text y="64" className="st-gate-name">Payment rail</text>
            <text y="80" className="st-gate-detail">sandbox</text>
            <text y="102" className="st-count"><tspan fill="var(--color-text-success)">✓ {counts.current[RAIL].pass} paid</tspan></text>
          </g>;
        })()}

        {/* Packets */}
        {live.current.map((p) => {
          const elapsed = now - p.launchAt;
          if (elapsed < 0) return null;
          const end = stopX(p);
          const travel = (end - X0) / SPEED;
          const progress = reduced ? 1 : Math.min(1, elapsed / travel);
          const x = X0 + (end - X0) * ease(progress);
          const y = laneY(p.lane);
          const arrived = progress >= 1;
          const hold = elapsed - travel;
          const opacity = hold > HOLD ? Math.max(0, 1 - (hold - HOLD) / FADE) : 1;
          const color = arrived ? RESULT_COLOR[p.result] : KIND_COLOR[p.kind];
          const ring = arrived && hold < 900 ? hold / 900 : null;
          return <g key={p.id} opacity={opacity} style={{ color }}>
            {!arrived && <rect x={x - 40} y={y - 1.5} width="40" height="3" rx="1.5" fill={color} opacity=".35" />}
            {ring !== null && <circle cx={x} cy={y} r={9 + ring * 22} fill="none" stroke={color} strokeWidth={2} opacity={1 - ring} />}
            <circle cx={x} cy={y} r="8" fill={color} filter="url(#st-glow)" />
            {arrived && p.result === 'block' && <path d={`M${x - 3.5} ${y - 3.5}l7 7m0-7l-7 7`} stroke="var(--color-surface-secondary)" strokeWidth="2" strokeLinecap="round" />}
            {arrived && p.result === 'pass' && <path d={`M${x - 4} ${y}l2.8 2.8 5-5.6`} fill="none" stroke="var(--color-surface-secondary)" strokeWidth="2" strokeLinecap="round" />}
            {!arrived && <text x={x} y={y - 14} className="st-who" fill={color}>{p.who}</text>}
          </g>;
        })}
      </svg>
    </div>
    <ol className="st-log" aria-live="polite">
      {notes.current.length === 0 && <li className="st-log-empty">Waiting for the first request…</li>}
      {notes.current.map((note, i) => {
        const Icon = NOTE_ICON[note.result];
        return <li key={note.id} className={`st-log-row ${note.result}`} style={{ opacity: 1 - i * 0.22 }}>
          <span className="st-log-icon"><Icon size={14} strokeWidth={2.6} /></span>
          <span className="st-log-text">{note.text}</span>
          <strong>{note.verdict}</strong>
        </li>;
      })}
    </ol>
  </div>;
}
