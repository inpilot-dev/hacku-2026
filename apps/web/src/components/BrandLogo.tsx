import { useId } from 'react';
import './brand-logo.css';

/** A purse with a cutout M and clasp accents in the three mascot colours. */
export default function BrandLogo() {
  const cutoutId = useId();
  return <span className="brand-logo">
    <svg className="brand-logo-mark" viewBox="0 0 64 64" width="30" height="30" fill="none" aria-hidden="true" focusable="false">
      <defs><mask id={cutoutId}>
        <rect width="64" height="64" fill="white" />
        <path d="M22 46V33L32 41L42 33V46" stroke="black" strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round" />
      </mask></defs>
      <path d="M22 15V22M32 15V22M42 15V22" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      <rect x="13" y="20" width="38" height="4" rx="2" fill="currentColor" />
      <rect x="6" y="23" width="52" height="34" rx="12" fill="currentColor" mask={`url(#${cutoutId})`} />
      <circle className="brand-logo-clasp brand-logo-clasp-1" cx="22" cy="12" r="4.5" fill="#ff922b" />
      <circle className="brand-logo-clasp brand-logo-clasp-2" cx="32" cy="12" r="4.5" fill="#26b873" />
      <circle className="brand-logo-clasp brand-logo-clasp-3" cx="42" cy="12" r="4.5" fill="#f65fa7" />
    </svg>
    <span className="brand-logo-wordmark">Mandate</span>
  </span>;
}
