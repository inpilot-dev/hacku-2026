import BrandLogo from '../components/BrandLogo';
import type { ReactNode } from 'react';
import { ArrowRight, Ban, CreditCard, EyeOff, Lock, Snowflake, Tag } from 'lucide-react';
import '../simple/openai-tokens.css';
import './landing.css';
import ReactiveCharacter from '../components/ReactiveCharacter';

/*
 * Product introduction for Mandate, in the simple flow's look. Everything here is static copy: the numbers in the
 * "Checked" section come from evaluation/results/latest.json and should be refreshed if the evaluation is rerun.
 */

type Name = 'kumi' | 'kip' | 'stella' | 'bean';

function Sticker({ name, state = 'idle', size = 88, tilt = 0 }: { name: Name; state?: string; size?: number; tilt?: number }) {
  return <span className="lp-sticker" style={{ width: size, height: size, transform: `rotate(${tilt}deg)` }}><ReactiveCharacter name={name} state={state} size={size} /></span>;
}

const STEPS: { who: Name; tilt: number; title: string; body: string }[] = [
  { who: 'bean', tilt: -4, title: 'You set the rules', body: 'A weekly allowance, a cap per order, the shops it may use and what it must never buy. Nothing runs until you confirm.' },
  { who: 'kumi', tilt: 5, title: 'Kumi packs the basket', body: 'The shopping agent turns “rice, milk, 3 apples” into products from shops you allowed. It never sees blocked items and never writes a price.' },
  { who: 'kip', tilt: -3, title: 'Kip decides', body: 'The wallet prices the basket itself, reserves the money atomically and pays, asks you, or refuses. The agent cannot overrule it.' },
  { who: 'stella', tilt: 4, title: 'Stella keeps the log', body: 'Every decision goes into a hash-chained audit trail, checked by an independent verifier that notices if anything is edited.' },
];

const CANNOT: { icon: ReactNode; text: string }[] = [
  { icon: <EyeOff size={18} />, text: 'See a reusable card number' },
  { icon: <Tag size={18} />, text: 'Set or change a price' },
  { icon: <Lock size={18} />, text: 'Go over the weekly or per-order limit, even with two purchases at once' },
  { icon: <Ban size={18} />, text: 'Buy a blocked category, such as alcohol' },
  { icon: <CreditCard size={18} />, text: 'Check out on its own' },
  { icon: <Snowflake size={18} />, text: 'Keep spending after you freeze or revoke' },
];

export default function Landing() {
  return <div className="lp">
    <header className="lp-top">
      <span className="lp-brand"><BrandLogo /></span>
      <nav>
        <a href="#how">How it works</a>
        <a href="#checked">Evidence</a>
        <a className="lp-nav-cta" href="./">Open the demo</a>
      </nav>
    </header>

    <section className="lp-hero">
      <div>
        <p className="lp-kicker">A wallet for AI shopping agents</p>
        <h1>Let an agent do the shopping. <em>Keep the money on a leash.</em></h1>
        <p className="lp-lede">Mandate gives a shopping agent a card with rules you set. The agent fills the basket; the wallet alone decides whether it gets paid for. Freeze it in one tap.</p>
        <div className="lp-actions">
          <a className="lp-cta" href="./">Try it with Mum’s groceries<ArrowRight size={18} /></a>
          <a className="lp-ghost" href="?card">See the virtual card</a>
        </div>
        <p className="lp-fine">Sandbox prototype. Payments are simulated and no real money moves.</p>
      </div>

      <div className="lp-demo" aria-label="Example: the wallet's decisions on three baskets">
        <div className="lp-demo-card">
          <div className="lp-demo-top"><span>Mum’s grocery card</span><span className="lp-status">Active</span></div>
          <small>Left this week</small>
          <b>HK$ 614.00</b>
          <div className="lp-meter" aria-hidden>{Array.from({ length: 20 }, (_, i) => <i key={i} className={i < 15 ? 'on' : ''} />)}</div>
          <div className="lp-demo-sticker"><Sticker name="kip" size={92} tilt={8} /></div>
        </div>
        <ul className="lp-verdicts">
          <li className="good"><span>Rice, milk, apples</span><b>Paid HK$ 186.00</b></li>
          <li className="bad"><span>Basket with wine</span><b>Refused · blocked item</b></li>
          <li className="ask"><span>Large household restock</span><b>Asks you first</b></li>
        </ul>
        <p className="lp-caption">Example decisions</p>
      </div>
    </section>

    <section className="lp-problem">
      <p>Agents can already fill a shopping cart. <span>Handing one your card is the scary part: a misread list, a prompt hidden in a product page, or two purchases racing each other can all spend money you never agreed to.</span></p>
    </section>

    <section className="lp-section" id="how">
      <p className="lp-kicker">How it works</p>
      <h2>Four characters, one rule: the wallet decides.</h2>
      <ol className="lp-steps">
        {STEPS.map((step, i) => <li key={step.who}>
          <Sticker name={step.who} size={84} tilt={step.tilt} />
          <span className="lp-step-n">{i + 1}</span>
          <h3>{step.title}</h3>
          <p>{step.body}</p>
        </li>)}
      </ol>
    </section>

    <section className="lp-section lp-split">
      <div>
        <p className="lp-kicker">Guardrails</p>
        <h2>What the agent can’t do</h2>
        <p className="lp-muted">These are enforced by the wallet and card issuer, not by asking the model nicely. The agent gets a scoped token and a control card it can never reuse.</p>
      </div>
      <ul className="lp-cannot">
        {CANNOT.map((item) => <li key={item.text}><span>{item.icon}</span>{item.text}</li>)}
      </ul>
    </section>

    <section className="lp-section" id="checked">
      <p className="lp-kicker">Checked, not just claimed</p>
      <h2>We tried to break it.</h2>
      <div className="lp-stats">
        <article><b>20 / 20</b><h3>Safety scenarios passed</h3><p>No unauthorised payment, no overspend and no false refusal across the deterministic evaluation.</p></article>
        <article><b>HK$ 0</b><h3>Overspent in a race</h3><p>Two concurrent HK$ 300 purchases against HK$ 400 left. An unsafe baseline paid both and went HK$ 200 over; Mandate paid one.</p></article>
        <article><b>5 / 5</b><h3>Tampering detected</h3><p>Edited, deleted, re-chained or truncated audit logs were all rejected by the independent verifier.</p></article>
      </div>
      <p className="lp-muted lp-note">A bounded Z3 model also finds the double-spend in the unsafe design and none in the atomic reservation. It covers that model and bound only, not the deployed wallet. Full details are under <a href="?classic">Activity &amp; safety</a>.</p>
    </section>

    <section className="lp-final">
      <div className="lp-final-cast"><Sticker name="kumi" size={72} tilt={-6} /><Sticker name="kip" size={72} tilt={4} /><Sticker name="stella" size={72} tilt={-3} /></div>
      <h2>Give Mum’s shopping to an agent, safely.</h2>
      <a className="lp-cta" href="./">Open the demo<ArrowRight size={18} /></a>
    </section>

    <footer className="lp-foot">
      <span>Mandate · HacKU 2026</span>
      <span>Prices come from a timestamped Wellcome catalog snapshot, not a live store connection.</span>
    </footer>
  </div>;
}
