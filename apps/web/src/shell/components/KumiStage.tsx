import ReactiveCharacter from '@/components/ReactiveCharacter';
import './kumi-stage.css';

export type KumiActivity = 'idle' | 'searching' | 'found' | 'packing' | 'added' | 'error';

const COPY: Record<KumiActivity, [string, string]> = {
  idle: ['Ready when you are', 'Tell me what you need.'],
  searching: ['Looking for your match', 'Checking products against your request.'],
  found: ['Something to review', 'Take a look at the product details.'],
  packing: ['Checking your store cart', 'Waiting for the shop to confirm the items.'],
  added: ['Your store cart matches', 'The items and quantities have been checked.'],
  error: ['Let’s take another look', 'Check the message below before continuing.'],
};

/** Decorative rig over the original transparent body. UI status remains readable without motion. */
export default function KumiStage({ activity, title, detail }: { activity: KumiActivity; title?: string; detail?: string }) {
  const copy = COPY[activity];
  const state = activity === 'error' ? 'sad' : activity === 'found' || activity === 'added' ? 'happy' : 'idle';
  return <div className="kumi-stage" data-activity={activity}>
    <div className="kumi-scene" aria-hidden="true">
      <svg className="kumi-rig" viewBox="0 0 260 150" fill="none">
        <ellipse cx="126" cy="131" rx="53" ry="6" fill="currentColor" opacity=".08" />
        <g className="kumi-leg kumi-leg-left">
          <path d="M111 94v25" stroke="#E98722" strokeWidth="10" strokeLinecap="round" />
          <ellipse cx="107" cy="123" rx="12" ry="6" fill="#F8A13A" />
        </g>
        <g className="kumi-leg kumi-leg-right">
          <path d="M143 94v25" stroke="#E98722" strokeWidth="10" strokeLinecap="round" />
          <ellipse cx="147" cy="123" rx="12" ry="6" fill="#F8A13A" />
        </g>
        <g className="kumi-arm kumi-arm-left">
          <path d="M98 66Q81 69 78 86" stroke="#F8A13A" strokeWidth="10" strokeLinecap="round" />
          <ellipse cx="77" cy="87" rx="7" ry="8" fill="#FFB14A" />
        </g>
        <g className="kumi-arm kumi-arm-right">
          <path d="M153 66Q164 75 178 65" stroke="#F8A13A" strokeWidth="10" strokeLinecap="round" />
          <g className="kumi-magnifier">
            <path d="m179 65 11-18" stroke="#647765" strokeWidth="6" strokeLinecap="round" />
            <circle cx="197" cy="35" r="17" fill="#EFF8F3" fillOpacity=".8" stroke="#647765" strokeWidth="5" />
            <path d="M190 28q4-4 10-3" stroke="white" strokeWidth="3" strokeLinecap="round" />
          </g>
          <ellipse cx="179" cy="65" rx="7" ry="8" fill="#FFB14A" />
        </g>
        <g className="kumi-basket">
          <path d="m194 102 5 28h41l5-28Z" fill="#DDEDE2" stroke="#5E896F" strokeWidth="2.5" strokeLinejoin="round" />
          <path d="m204 102 10-15m20 15-10-15M207 109l2 14m12-14v14m13-14-2 14" stroke="#5E896F" strokeWidth="3" strokeLinecap="round" />
        </g>
        <g className="kumi-parcel">
          <rect x="165" y="51" width="22" height="24" rx="5" fill="#CBE4D2" stroke="#5E896F" strokeWidth="2" />
          <path d="M172 51v7h8v-7" stroke="#5E896F" strokeWidth="2" />
        </g>
        <g className="kumi-cart-check">
          <circle cx="238" cy="98" r="11" fill="#31704F" />
          <path d="m233 98 3 3 6-7" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        </g>
      </svg>
      <div className="kumi-body"><ReactiveCharacter name="kumi" state={state} size={112} /></div>
    </div>
    <div className="kumi-stage-copy"><span>Kumi · Shopping companion</span><strong>{title ?? copy[0]}</strong><p>{detail ?? copy[1]}</p></div>
  </div>;
}
