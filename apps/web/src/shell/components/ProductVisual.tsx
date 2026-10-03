import { Carrot, Cherry, Milk, Package2, Wine } from 'lucide-react';

/** Category artwork, not a photograph of a particular merchant product. */
export default function ProductVisual({ title, kind }: { title: string; kind?: 'charger' | 'earbuds' | 'phone' }) {
  const category = kind ?? (/charger|充電/i.test(title) ? 'charger' : /earbud|headphone|earphone/i.test(title) ? 'earbuds' : /phone|smartphone/i.test(title) ? 'phone' : 'other');
  if (category === 'other') {
    const Icon = /milk|牛奶/i.test(title) ? Milk : /apple|grape|berry|fruit|kiwi/i.test(title) ? Cherry : /broccoli|vegetable|carrot|green/i.test(title) ? Carrot : /wine|champagne|alcohol/i.test(title) ? Wine : Package2;
    return <span className="product-art product-art-grocery" aria-hidden="true"><Icon strokeWidth={1.5} /></span>;
  }
  return <span className={`product-art product-art-${category}`} aria-hidden="true">
    <svg viewBox="0 0 160 130" fill="none">
      <ellipse cx="80" cy="112" rx="40" ry="6" fill="currentColor" opacity=".09" />
      {category === 'charger' && <g transform="rotate(-12 80 65)">
        <path d="M98 24v-12M111 27V15" stroke="#8b9197" strokeWidth="7" strokeLinecap="round" />
        <path d="m48 42 25-13h42l-18 13v58l-24 12-25-12Z" fill="#d7dedc" />
        <path d="M48 42h49v58H48Z" fill="#fafcfb" stroke="#c5cec9" strokeWidth="1.5" />
        <path d="m97 42 18-13v59l-18 12Z" fill="#b4c2bb" />
        <rect x="59" y="55" width="25" height="8" rx="4" fill="#435e50" />
        <rect x="59" y="73" width="25" height="8" rx="4" fill="#435e50" />
        <path d="m105 54 5-3m-5 12 5-3" stroke="#f5f9f7" strokeWidth="2" strokeLinecap="round" />
      </g>}
      {category === 'earbuds' && <>
        <rect x="43" y="65" width="77" height="41" rx="19" fill="#e6e5f0" stroke="#bbb9d2" strokeWidth="1.5" />
        <path d="M46 82h71" stroke="#bbb9d2" /><circle cx="82" cy="94" r="2" fill="#85819e" />
        <g transform="rotate(-18 61 40)"><rect x="54" y="34" width="13" height="33" rx="6.5" fill="#f8f8ff" stroke="#bebbd3" /><ellipse cx="56" cy="31" rx="14" ry="10" fill="#efedf8" stroke="#bebbd3" /><ellipse cx="48" cy="31" rx="4" ry="5" fill="#77758c" /></g>
        <g transform="rotate(18 103 39)"><rect x="96" y="33" width="13" height="33" rx="6.5" fill="#f8f8ff" stroke="#bebbd3" /><ellipse cx="108" cy="30" rx="14" ry="10" fill="#efedf8" stroke="#bebbd3" /><ellipse cx="116" cy="30" rx="4" ry="5" fill="#77758c" /></g>
      </>}
      {category === 'phone' && <g transform="rotate(10 80 64)">
        <rect x="54" y="12" width="56" height="98" rx="12" fill="#889aab" />
        <rect x="49" y="10" width="56" height="98" rx="12" fill="#344c64" />
        <rect x="53" y="14" width="48" height="90" rx="9" fill="#d1e4f2" />
        <path d="M53 70c19-30 38-5 48-31v55a10 10 0 0 1-10 10H63a10 10 0 0 1-10-10Z" fill="#8db0cd" />
        <path d="M53 85c20-16 29 8 48-5v14a10 10 0 0 1-10 10H63a10 10 0 0 1-10-10Z" fill="#557c9f" />
        <rect x="65" y="18" width="25" height="5" rx="2.5" fill="#344c64" />
      </g>}
    </svg>
  </span>;
}
