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

/** Floating companion with decorative props; status remains readable without motion. */
export default function KumiStage({ activity, title, detail }: { activity: KumiActivity; title?: string; detail?: string }) {
  const copy = COPY[activity];
  const state = activity === 'found' || activity === 'added' ? 'happy' : 'idle';
  return <div className="kumi-stage" data-activity={activity}>
    <div className="kumi-scene" aria-hidden="true">
      <svg className="kumi-rig" viewBox="0 0 260 150" fill="none">
        <ellipse className="kumi-shadow" cx="122" cy="123" rx="39" ry="5" fill="currentColor" opacity=".09" />
        <g className="kumi-magnifier">
          <path d="m184 63 10-16" stroke="#809C8B" strokeWidth="5" strokeLinecap="round" />
          <circle cx="200" cy="36" r="14" fill="#EFF8F3" fillOpacity=".85" stroke="#809C8B" strokeWidth="3.5" />
          <path d="M195 30q3-3 7-2" stroke="white" strokeWidth="2.5" strokeLinecap="round" />
        </g>
        <g className="kumi-sparkles" stroke="#F4B94B" strokeWidth="2.5" strokeLinecap="round">
          <path d="M177 25v10m-5-5h10M73 48v7m-3.5-3.5h7M166 75v6m-3-3h6" />
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
