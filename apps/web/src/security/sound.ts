/*
 * Synthesized sound effects for the harness stage (Web Audio, no audio files). Everything runs through one bus with
 * a gentle compressor and a short generated reverb so the sounds feel like one set. Browsers only allow sound after
 * a user gesture, so the context is created lazily and resumed on the first click anywhere on the page.
 * The on/off choice is a per-device convenience kept in localStorage.
 */

export type Sfx = 'block' | 'pass' | 'paid' | 'review' | 'launch';

const KEY = 'mandate-security-sound';
// A major pentatonic run, one note per gate, so a request crossing the harness plays a rising phrase.
const GATE_NOTES = [659.25, 739.99, 880, 987.77, 1108.73, 1318.51];

type Bus = { ac: AudioContext; dry: GainNode; wet: GainNode; noise: AudioBuffer };
let bus: Bus | null = null;
let lastAt = 0;
let enabled = (() => {
  try { return localStorage.getItem(KEY) !== 'off'; } catch { return true; }
})();
const listeners = new Set<(on: boolean) => void>();

function build(ac: AudioContext): Bus {
  const compressor = ac.createDynamicsCompressor();
  compressor.threshold.value = -20;
  compressor.knee.value = 18;
  compressor.ratio.value = 4;
  compressor.attack.value = 0.003;
  compressor.release.value = 0.2;
  const master = ac.createGain();
  master.gain.value = 0.75;
  compressor.connect(master).connect(ac.destination);

  // Short room: exponentially decaying stereo noise as the impulse response.
  const length = Math.floor(ac.sampleRate * 1.1);
  const impulse = ac.createBuffer(2, length, ac.sampleRate);
  for (let channel = 0; channel < 2; channel++) {
    const data = impulse.getChannelData(channel);
    for (let i = 0; i < length; i++) data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, 3.2);
  }
  const reverb = ac.createConvolver();
  reverb.buffer = impulse;
  const wet = ac.createGain();
  wet.gain.value = 0.22;
  wet.connect(reverb).connect(compressor);
  const dry = ac.createGain();
  dry.connect(compressor);

  const noise = ac.createBuffer(1, ac.sampleRate, ac.sampleRate);
  const n = noise.getChannelData(0);
  for (let i = 0; i < n.length; i++) n[i] = Math.random() * 2 - 1;
  return { ac, dry, wet, noise };
}

function getBus() {
  if (!bus) {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    bus = build(new Ctor());
  }
  if (bus.ac.state === 'suspended') void bus.ac.resume();
  return bus;
}

if (typeof window !== 'undefined') {
  window.addEventListener('pointerdown', () => { if (enabled) getBus(); }, { once: true });
}

export function soundOn() { return enabled; }

export function setSound(on: boolean) {
  enabled = on;
  try { localStorage.setItem(KEY, on ? 'on' : 'off'); } catch { /* convenience only */ }
  if (on) getBus();
  listeners.forEach((listener) => listener(on));
}

export function onSoundChange(listener: (on: boolean) => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Route a node to the dry bus and, scaled, to the reverb. */
function out(b: Bus, node: AudioNode, wet = 1) {
  node.connect(b.dry);
  if (wet > 0) {
    const send = b.ac.createGain();
    send.gain.value = wet;
    node.connect(send).connect(b.wet);
  }
}

/** Percussive envelope: fast attack, exponential decay. */
function env(b: Bus, at: number, peak: number, decay: number, attack = 0.005) {
  const g = b.ac.createGain();
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(peak, at + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, at + attack + decay);
  return g;
}

function osc(b: Bus, type: OscillatorType, freq: number, at: number, stop: number, detune = 0) {
  const o = b.ac.createOscillator();
  o.type = type;
  o.frequency.setValueAtTime(freq, at);
  o.detune.value = detune;
  o.start(at);
  o.stop(stop);
  return o;
}

/** Glassy pluck: sine plus a quiet octave partial. */
function pluck(b: Bus, at: number, freq: number, peak: number, decay: number, wet = 0.8) {
  const g = env(b, at, peak, decay);
  osc(b, 'sine', freq, at, at + decay + 0.05).connect(g);
  const partial = env(b, at, peak * 0.25, decay * 0.5);
  osc(b, 'sine', freq * 2, at, at + decay + 0.05).connect(partial);
  out(b, g, wet);
  out(b, partial, wet);
}

/** Marimba-like tone: a sine with a short, quiet 4x partial for the wooden attack. */
function marimba(b: Bus, at: number, freq: number, peak: number, decay: number) {
  const g = env(b, at, peak, decay, 0.003);
  osc(b, 'sine', freq, at, at + decay + 0.05).connect(g);
  const click = env(b, at, peak * 0.3, 0.04, 0.002);
  osc(b, 'sine', freq * 4, at, at + 0.08).connect(click);
  out(b, g, 0.5);
  out(b, click, 0.2);
}

/** FM bell: a sine carrier modulated at a non-integer ratio gives a metallic, coin-like ring. */
function bell(b: Bus, at: number, freq: number, peak: number, decay: number) {
  const mod = osc(b, 'sine', freq * 3.5, at, at + decay + 0.05);
  const depth = b.ac.createGain();
  depth.gain.setValueAtTime(freq * 2.2, at);
  depth.gain.exponentialRampToValueAtTime(1, at + decay);
  const carrier = osc(b, 'sine', freq, at, at + decay + 0.05);
  mod.connect(depth).connect(carrier.frequency);
  const g = env(b, at, peak, decay, 0.003);
  carrier.connect(g);
  out(b, g, 1);
}

function noiseBurst(b: Bus, at: number, type: BiquadFilterType, from: number, to: number, peak: number, decay: number, q = 1, wet = 0.6) {
  const src = b.ac.createBufferSource();
  src.buffer = b.noise;
  const filter = b.ac.createBiquadFilter();
  filter.type = type;
  filter.Q.value = q;
  filter.frequency.setValueAtTime(from, at);
  filter.frequency.exponentialRampToValueAtTime(to, at + decay);
  const g = env(b, at, peak, decay, 0.01);
  src.connect(filter).connect(g);
  out(b, g, wet);
  src.start(at);
  src.stop(at + decay + 0.05);
}

const wobble = () => 1 + (Math.random() - 0.5) * 0.012; // tiny pitch variety so repeats don't sound robotic

export function play(kind: Sfx, gate = 0) {
  if (!enabled) return;
  const b = getBus();
  if (!b || b.ac.state !== 'running') return;
  const now = b.ac.currentTime + 0.01;
  // A swarm lands many packets at once; keep it to a burst, not a wall of sound.
  if (kind !== 'launch' && now - lastAt < 0.08) return;
  lastAt = now;

  switch (kind) {
    case 'pass': {
      const note = GATE_NOTES[Math.min(gate, GATE_NOTES.length - 1)] * wobble();
      pluck(b, now, note, 0.16, 0.35);
      break;
    }
    case 'block': {
      // Clean "nuh-uh": a soft low sine knock under a marimba-like falling minor third. No noise, no dissonance.
      const knock = env(b, now, 0.32, 0.18, 0.003);
      const low = osc(b, 'sine', 196 * wobble(), now, now + 0.25);
      low.frequency.exponentialRampToValueAtTime(98, now + 0.16);
      low.connect(knock);
      out(b, knock, 0.2);
      marimba(b, now, 523.25 * wobble(), 0.15, 0.22);
      marimba(b, now + 0.13, 440 * wobble(), 0.15, 0.3);
      break;
    }
    case 'review': {
      // Doorbell: a soft descending major third, waiting for the owner.
      pluck(b, now, 987.77, 0.14, 0.5, 1);
      pluck(b, now + 0.16, 783.99, 0.14, 0.7, 1);
      break;
    }
    case 'paid': {
      // Cha-ching: two bright bells a fifth apart, with a short shimmer of high noise.
      bell(b, now, 1318.51, 0.16, 0.5);
      bell(b, now + 0.09, 1975.53, 0.14, 0.9);
      noiseBurst(b, now + 0.09, 'highpass', 7000, 9000, 0.05, 0.25, 0.5, 1);
      pluck(b, now + 0.09, 659.25, 0.08, 0.6, 1);
      break;
    }
    case 'launch': {
      // Whoosh up: band-passed noise sweeping upward over a rising sub tone.
      noiseBurst(b, now, 'bandpass', 300, 4200, 0.35, 0.7, 2.5, 0.8);
      const g = env(b, now, 0.18, 0.6, 0.15);
      const sub = osc(b, 'sine', 70, now, now + 0.8);
      sub.frequency.exponentialRampToValueAtTime(220, now + 0.6);
      sub.connect(g);
      out(b, g, 0.3);
      break;
    }
  }
}
