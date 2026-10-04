import { useEffect, useState } from 'react';
import type { Policy } from '../../../../../contracts/types';
import { commerce, type Preview } from '@/lib/commerce';
import { money } from '@/lib/format';
import { useLocale } from '@/lib/locale';
export default function RulePreview({ policy, token }: { policy: Policy; token: string }) {
  const [value, setValue] = useState<Preview | null>(null);
  const [error, setError] = useState('');
  const { t } = useLocale();
  useEffect(() => {
    let active = true;
    setValue(null); setError('');
    const timer = setTimeout(() => { commerce.preview(token, policy).then((r) => { if (active) setValue(r); }).catch((e) => { if (active) setError(e.message); }); }, 200);
    return () => { active = false; clearTimeout(timer); };
  }, [policy, token]);
  return <section className="space-y-2 rounded-xl border p-3" aria-label={t('Permission preview', '授權預覽')}>
    <h3 className="font-medium">{t('What these rules allow', '這些規則允許甚麼')}</h3>
    <p className="text-xs text-muted-foreground">{t('Illustrative amounts, evaluated by the wallet. No money is reserved. Risk history and live availability are checked again at purchase.', '示例金額由錢包規則判斷，不會預留款項。購買時會再次檢查風險紀錄及供應情況。')}</p>
    {error && <p role="alert">{error}</p>}
    {!value && !error && <p role="status">{t('Checking…', '檢查中…')}</p>}
    {value?.examples.map((e) => <div key={e.id} className="rounded-lg bg-muted/40 p-2 text-sm">
      <strong>{money(e.total_minor)} · {e.status === 'approved' ? t('Allowed', '允許') : e.status === 'requires_review' ? t('Ask first', '先確認') : t('Refused', '拒絕')}</strong>
      <ul>{e.violations.map((v, i) => <li key={`${v.rule_id}-${i}`}>{v.message}</li>)}</ul>
      <details className="text-xs"><summary>{t('Rules checked', '已檢查規則')}</summary><ul>{e.rule_ids.map((id) => <li key={id}>{id}</li>)}</ul></details>
    </div>)}
  </section>;
}
