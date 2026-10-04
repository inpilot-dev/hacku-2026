import type { AgentComparison, CatalogResponse } from '../../../../../contracts/types';
import { money } from '@/lib/format';
import { useLocale } from '@/lib/locale';
export default function StoreComparison({ comparisons, evidence }: { comparisons: AgentComparison[]; evidence: CatalogResponse['evidence'] }) {
  const { t } = useLocale();
  if (!comparisons.length) return null;
  return <section className="mt-3 rounded-xl border p-3" aria-label="Store comparison"><h3 className="font-medium">{t('Why this basket?', '為何選擇這個購物籃？')}</h3>
    <p className="text-xs text-muted-foreground">{t('Most list items found, then lowest captured total. Different packs are not claimed equivalent. These are priced candidates; the wallet checks permission at checkout.', '先考慮找到最多清單項目，再比較已記錄總額。不同包裝不視為相同商品。這是報價候選，結帳時錢包再檢查授權。')}</p>
    <div className="overflow-auto"><table className="mt-2 w-full text-left text-sm"><thead><tr><th>{t('Store', '商店')}</th><th>{t('Items matched', '匹配項目')}</th><th>{t('Total', '總額')}</th><th>{t('Missing / excluded', '缺少／排除')}</th></tr></thead><tbody>{comparisons.map((c) => <tr key={c.merchant_id}><td className="p-2">{c.merchant_id}</td><td>{c.matched_items}</td><td>{c.quote ? money(c.quote.total_minor) : '—'}</td><td>{c.problem || c.missing_items.join(', ') || '—'}</td></tr>)}</tbody></table></div>
    {comparisons.map((c) => c.quote && <details key={c.merchant_id} className="text-xs"><summary>{c.merchant_id} · {t('Price and delivery evidence', '價格及運費證據')}</summary><p>{t('Delivery / other charges', '運費／其他費用')}: {money(c.quote.charges.reduce((n, ch) => n + ch.amount_minor, 0))}</p><ul>{evidence.filter((e) => c.quote!.evidence_ids.includes(e.id)).map((e) => <li key={e.id}><a href={e.source_url} target="_blank" rel="noopener noreferrer" className="underline">{e.kind}</a> · {e.observed_at} · {e.conditions}</li>)}</ul></details>)}
  </section>;
}
