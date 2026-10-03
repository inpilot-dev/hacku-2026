import { useId } from 'react';
import './brand-logo.css';

/** A simple wallet with an M cutout and the three mascot colours blended together. */
export default function BrandLogo() {
  const id = useId();
  const cutoutId = `${id}-cutout`;
  const gradientId = `${id}-gradient`;
  return <span className="brand-logo">
    <svg className="brand-logo-mark" viewBox="0 0 64 64" width="30" height="30" fill="none" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={gradientId} x1="6" y1="12" x2="58" y2="56" gradientUnits="userSpaceOnUse">
          <stop stopColor="#ff922b" /><stop offset=".5" stopColor="#26b873" /><stop offset="1" stopColor="#f65fa7" />
        </linearGradient>
        <mask id={cutoutId}>
          <rect width="64" height="64" fill="white" />
          <path d="M22 44V29L32 37L42 29V44" stroke="black" strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round" />
        </mask>
      </defs>
      <rect x="6" y="12" width="52" height="44" rx="14" fill={`url(#${gradientId})`} mask={`url(#${cutoutId})`} />
      <path d="M18 21H46" stroke="white" strokeOpacity=".3" strokeWidth="2" strokeLinecap="round" />
    </svg>
    <span className="brand-logo-wordmark">Mandate</span>
  </span>;
}
