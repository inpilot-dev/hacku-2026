import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { request } from '@/lib/api';
import { commerce, type Operation, type Plans, type Study } from '@/lib/commerce';
import { useLocale } from '@/lib/locale';
import { money } from '@/lib/format';
import { TOKEN, useAccount } from '../data/account';
import type { Quote } from '../../../../../contracts/types';
import { storeName } from './AllowanceSetup';

function download(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
}
function base64(bytes: Uint8Array) { return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }

export default function CommerceTools({ quote = null }: { quote?: Quote | null }) {
  const account = useAccount();
  const { t } = useLocale();
  const [quoteId, setQuoteId] = useState(quote?.id ?? '');
  const [selected, setSelected] = useState<Quote | null>(quote);
  const [hash, setHash] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [plans, setPlans] = useState<Plans | null>(null);
  const [studies, setStudies] = useState<Study[]>([]);
  const [participant, setParticipant] = useState('P01');
  const [mode, setMode] = useState<'manual' | 'agent'>('manual');
  const [execution, setExecution] = useState<'human' | 'model' | 'fallback' | 'scripted'>('human');
  const [actions, setActions] = useState(0);
  const [errors, setErrors] = useState(0);
  const [setupSeconds, setSetupSeconds] = useState(0);
  const [sourceUrl, setSourceUrl] = useState('');
  const [observation, setObservation] = useState('');
  const [operations, setOperations] = useState<Operation[]>([]);
  const [configured, setConfigured] = useState(false);
  const [scenario, setScenario] = useState<'happy' | 'order_failure' | 'lost_capture_response' | 'pending_refund' | 'failed_refund'>('happy');
  const [credential, setCredential] = useState('');
  const [credentialResult, setCredentialResult] = useState('');
  useEffect(() => { setQuoteId(quote?.id ?? ''); setSelected(quote); setHash(''); setPlans(null); }, [quote]);
  async function refresh() {
    const [s, o, p] = await Promise.all([commerce.studies(TOKEN), commerce.operations(TOKEN), commerce.provider(TOKEN)]);
    setStudies(s.runs); setOperations(o.operations); setConfigured(p.configured);
  }
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError('');
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : 'Request failed'); } finally { setBusy(false); }
  }
  async function loadQuote() {
    const result = await commerce.approval(TOKEN, quoteId);
    setSelected(result.quote); setHash(result.quote_hash);
    const catalog = await request<{ evidence: Array<{ id: string; source_url: string; observed_at: string }> }>('/catalog', TOKEN);
    const evidence = catalog.evidence.find((e) => result.quote.evidence_ids.includes(e.id));
    setSourceUrl(evidence?.source_url ?? 'snapshot:' + result.quote.id); setObservation(evidence?.observed_at ?? '');
  }
  const active = studies.find((s) => !s.finished_at);
  const canStart = selected && hash && account.active;
  return <details className="mt-5 rounded-xl border p-4" onToggle={(e) => { if (e.currentTarget.open) void perform(refresh); }}>
    <summary className="cursor-pointer font-medium">{t('Compare, measure and test payments', '比較、量度及測試付款')}</summary>
    <div className="mt-4 space-y-5">
      <p className="text-sm text-muted-foreground">{t('Use an observed basket as a fixed task. Tests never place a retailer order.', '以觀測到的購物籃作固定任務。測試不會向商戶下單。')}</p>
      {!quote && <label className="block text-sm">{t('Saved basket', '已儲存購物籃')}<select className="w-full rounded border p-2" value={quoteId} onChange={(e) => { setQuoteId(e.target.value); setHash(''); setSelected(null); }}><option value="">{t('Build a basket in Groceries or choose a receipt', '在日用品建立購物籃或選擇收據')}</option>{account.receipts.records.filter((r) => r.quote).map((r) => <option key={r.receipt.id} value={r.quote!.id}>{storeName(r.quote!.merchant_id)} · {money(r.quote!.total_minor)} · {new Date(r.occurredAt).toLocaleDateString()}</option>)}</select></label>}
      <Button variant="outline" disabled={busy || !quoteId} onClick={() => void perform(loadQuote)}>{t('Review exact basket', '檢視確切購物籃')}</Button>
      {selected && <p className="text-sm">{selected.merchant_id} · {money(selected.total_minor)} · {selected.items.map((i) => `${i.title} × ${i.quantity}`).join(', ')}</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

      <section className="space-y-2" aria-label="Basket planning">
        <h3 className="font-medium">{t('Compare complete basket plans', '比較整個購物籃方案')}</h3>
        <p className="text-xs text-muted-foreground">{t('Exact title and pack only. Quantities stay fixed. Shipping comes from captured rules. Plans require separate review; nothing is purchased.', '只比較相同名稱及包裝，數量不變，運費來自已記錄規則。方案須另行審核，不會購買。')}</p>
        <Button variant="outline" disabled={busy || !selected || !account.mandateId} onClick={() => void perform(async () => {
          setPlans(await commerce.plans(TOKEN, { mandate_id: account.mandateId, items: selected!.items.map((i) => ({ product_id: i.product_id, quantity: i.quantity })) }));
        })}>{t('Find basket combinations', '尋找購物籃組合')}</Button>
        {plans && <><p className="text-xs">{plans.reason} · {plans.evaluated} {t('combinations checked', '個組合已檢查')}{plans.truncated ? t(' · search limit reached', ' · 已達搜尋上限') : ''}</p>
          {!plans.plans.length && <p>{t('No feasible observed plan. Change your list or explicitly revise your allowance.', '沒有可行的觀測方案。請修改清單或明確修改授權。')}</p>}
          {plans.plans.map((p, i) => <div key={i} className="rounded-lg bg-muted p-3 text-sm"><strong>{money(p.total_minor)} · {p.store_count} {t('stores', '間商店')}</strong>{p.orders.map((q) => <div key={q.merchant_id}><p>{storeName(q.merchant_id)}: {money(q.total_minor)}</p><ul>{q.items.map((item) => <li key={item.product_id}>{item.title} × {item.quantity}</li>)}{q.charges.map((charge, n) => <li key={n}>{charge.label}: {money(charge.amount_minor)}</li>)}</ul></div>)}</div>)}</>}
      </section>

      <section className="space-y-2" aria-label="Participant comparison">
        <h3 className="font-medium">{t('Manual / agent task comparison', '人手／代理任務比較')}</h3>
        <p className="text-xs text-muted-foreground">{t('Start BEFORE repeating the same shopping task, then stop at reviewed basket readiness. Record failures too. Server time is not independent proof of human participation.', '重做相同購物任務前開始計時，在購物籃審核完成時停止。失敗也須記錄。伺服器計時不代表已獨立核實真人參與。')}</p>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="text-sm">{t('Participant code', '參與者代碼')}<Input value={participant} onChange={(e) => setParticipant(e.target.value)} placeholder="P01" /></label>
          <label className="text-sm">{t('Route', '操作方式')}<select className="w-full rounded border p-2" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}><option value="manual">{t('Manual', '人手')}</option><option value="agent">{t('Agent', '代理')}</option></select></label>
          <label className="text-sm">{t('Execution', '執行模式')}<select className="w-full rounded border p-2" value={execution} onChange={(e) => setExecution(e.target.value as typeof execution)}><option value="human">Human</option><option value="model">Live model</option><option value="fallback">Fallback</option><option value="scripted">Scripted</option></select></label>
          <label className="text-sm">{t('Setup seconds (separate)', '設定秒數（分開記錄）')}<Input type="number" min="0" value={setupSeconds} onChange={(e) => setSetupSeconds(Number(e.target.value))} /></label>
        </div>
        <Button disabled={busy || !selected || !!active} onClick={() => void perform(async () => { await commerce.startStudy(TOKEN, { participant, task_id: selected!.basket_hash, mode, quote_id: selected!.id, setup_seconds: setupSeconds, execution_mode: execution }); await refresh(); })}>{t('Start trial', '開始測試')}</Button>
        {active && <div className="space-y-2 rounded-lg bg-muted p-3"><p>{active.participant} · {active.mode} · {t('Started', '已開始')} {new Date(active.started_at).toLocaleTimeString()}</p>
          <label className="block text-sm">{t('Actions', '操作次數')}<Input type="number" min="0" value={actions} onChange={(e) => setActions(Number(e.target.value))} /></label>
          <label className="block text-sm">{t('Errors', '錯誤次數')}<Input type="number" min="0" value={errors} onChange={(e) => setErrors(Number(e.target.value))} /></label>
          <label className="block text-sm">{t('Source URL / snapshot reference', '來源網址／快照')}<Input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} /></label>
          <label className="block text-sm">{t('Price observation time (ISO with timezone)', '價格觀測時間（ISO格式及時區）')}<Input value={observation} onChange={(e) => setObservation(e.target.value)} /></label>
          <div className="flex flex-wrap gap-2">{(['ready', 'failed', 'abandoned'] as const).map((outcome) => <Button key={outcome} variant="outline" disabled={busy || !sourceUrl || !observation} onClick={() => void perform(async () => { await commerce.finishStudy(TOKEN, active.id, { actions, errors, outcome, source_url: sourceUrl, observed_at: observation, note: 'Recorded by participant/observer. Basket readiness, no purchase.' }); await refresh(); })}>{t('Finish: ', '完成：')}{outcome}</Button>)}</div>
        </div>}
        <div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr><th>{t('Participant', '參與者')}</th><th>{t('Route', '方式')}</th><th>{t('Seconds', '秒')}</th><th>{t('Actions / errors', '操作／錯誤')}</th><th>{t('Outcome', '結果')}</th></tr></thead><tbody>{studies.filter((s) => s.finished_at).map((s) => <tr key={s.id}><td>{s.participant}</td><td>{s.mode} / {s.execution_mode}</td><td>{s.elapsed_seconds}</td><td>{s.actions} / {s.errors}</td><td>{s.outcome}</td></tr>)}</tbody></table></div>
        <Button variant="outline" onClick={() => download('mandate-study-records.json', studies)}>{t('Export raw trials', '匯出原始記錄')}</Button>
      </section>

      <section className="space-y-2" aria-label="Sandbox payment">
        <h3 className="font-medium">{t('Sandbox payment and recovery', '沙盒付款及復原')}</h3>
        <p className="text-xs text-muted-foreground">{t('Local simulation, no real funds or retailer orders. Your allowance stays accounted until the sandbox confirms capture, cancellation or refund. Unknown results retain the hold.', '本機模擬，不涉及真實資金或商戶訂單。額度會保留至沙盒確認扣款、取消或退款。結果不明時不會釋放預留。')}</p>
        <p className="text-sm">{configured ? t('Local sandbox ready · no API key needed', '本機沙盒已準備好 · 無需 API 金鑰') : t('Loading sandbox status…', '載入沙盒狀態…')}</p>
        <label className="block text-sm">{t('Synthetic order scenario', '模擬訂單場景')}<select className="w-full rounded border p-2" value={scenario} onChange={(e) => setScenario(e.target.value as typeof scenario)}><option value="happy">{t('Order confirmed', '訂單確認')}</option><option value="order_failure">{t('Captured → order fails → refund', '扣款 → 訂單失敗 → 退款')}</option><option value="lost_capture_response">{t('Capture response lost → retrieve', '扣款回應遺失 → 核對')}</option><option value="pending_refund">{t('Refund pending → retrieve again', '退款待處理 → 再次核對')}</option><option value="failed_refund">{t('Refund fails → retain accounting', '退款失敗 → 保留帳目')}</option></select></label>
        <Button disabled={busy || !canStart || !configured} onClick={() => void perform(async () => {
          const keyName = `mandate-sandbox-${selected!.id}-${scenario}`;
          const key = localStorage.getItem(keyName) ?? crypto.randomUUID(); localStorage.setItem(keyName, key);
          const job = await commerce.startOperation(TOKEN, { mandate_id: account.mandateId, quote_id: selected!.id, approved_quote_hash: hash, scenario }, key);
          await commerce.advance(TOKEN, job.id); await refresh(); await account.refresh();
        })}>{t('Approve exact basket in sandbox', '在沙盒批准此購物籃')}</Button>
        {operations.map((o) => <div key={o.id} className="space-y-2 rounded-lg border p-3 text-sm">
          <strong>{money(o.amount_minor)} · {o.status}</strong><p>{t('Payment', '付款')}: {o.payment_state} · {t('Order', '訂單')}: {o.order_state} · {t('Recovery', '復原')}: {o.recovery_state} · {t('Budget', '額度')}: {o.budget_state}</p>
          <div className="flex gap-2"><Button variant="outline" disabled={busy} onClick={() => void perform(async () => { await commerce.advance(TOKEN, o.id); await refresh(); await account.refresh(); })}>{t('Retrieve same operation', '核對同一交易')}</Button>
          {o.budget_state === 'held' && <Button variant="outline" disabled={busy} onClick={() => void perform(async () => { await commerce.cancel(TOKEN, o.id); await refresh(); await account.refresh(); })}>{t('Request cancellation', '要求取消')}</Button>}</div>
          <details><summary>{t('Evidence', '證據')}</summary><p>{o.boundary}</p><ol>{o.events.map((e, i) => <li key={i}>{e.at} {e.message}</li>)}</ol></details>
        </div>)}
      </section>
      <section className="space-y-2" aria-label="Portable signed permission">
        <h3 className="font-medium">{t('Sign and verify a portable permission', '簽署及核實可攜授權')}</h3>
        <p className="text-xs text-muted-foreground">{t('Your browser signs an export with a pinned owner key. This proves that key signed the current rules; it grants no extra authority and is not legal identity or bank support. Keep this device key to sign again.', '瀏覽器使用固定的擁有人金鑰簽署。這只證明該金鑰簽署現行規則，不增加權限，也不代表法律身分或銀行支援。請保留此裝置金鑰。')}</p>
        <Button variant="outline" disabled={busy || !account.active} onClick={() => void perform(async () => {
          if (!crypto.subtle) throw new Error('Signing requires a secure origin or localhost.');
          const db = await new Promise<IDBDatabase>((resolve, reject) => { const r = indexedDB.open('mandate-owner-key', 1); r.onupgradeneeded = () => r.result.createObjectStore('keys'); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
          const read = db.transaction('keys').objectStore('keys').get('owner');
          let pair = await new Promise<CryptoKeyPair | undefined>((resolve, reject) => { read.onsuccess = () => resolve(read.result); read.onerror = () => reject(read.error); });
          if (!pair) {
            pair = await crypto.subtle.generateKey({ name: 'Ed25519' }, false, ['sign', 'verify']) as CryptoKeyPair;
            const tx = db.transaction('keys', 'readwrite'); tx.objectStore('keys').put(pair, 'owner');
            await new Promise<void>((resolve, reject) => { tx.oncomplete = () => resolve(); tx.onerror = () => reject(tx.error); });
          }
          db.close();
          const publicKey = await crypto.subtle.exportKey('jwk', pair.publicKey);
          const template = await request<{ issuer: string } & Record<string, unknown>>('/commerce/credentials/template', TOKEN, { method: 'POST', body: JSON.stringify({ mandate_id: account.mandateId, public_x: publicKey.x }) });
          const header = { alg: 'EdDSA', typ: 'vc+jwt', kid: `${template.issuer}#${template.issuer.replace('did:key:', '')}` };
          const data = `${base64(new TextEncoder().encode(JSON.stringify(header)))}.${base64(new TextEncoder().encode(JSON.stringify(template)))}`;
          const signature = await crypto.subtle.sign('Ed25519', pair.privateKey, new TextEncoder().encode(data));
          const signed = `${data}.${base64(new Uint8Array(signature))}`;
          setCredential(signed); setCredentialResult('');
          download('mandate-credential.json', { type: 'EnvelopedVerifiableCredential', '@context': ['https://www.w3.org/ns/credentials/v2'], id: 'data:application/vc+jwt,' + signed });
        })}>{t('Sign and export current permission', '簽署及匯出現行授權')}</Button>
        <label className="block text-sm">{t('Compact signed credential', '已簽署授權')}<textarea className="w-full rounded border p-2 text-sm" value={credential} onChange={(e) => setCredential(e.target.value)} /></label>
        <Button variant="outline" disabled={busy || !credential} onClick={() => void perform(async () => { const r = await request<{ scope: string }>('/commerce/credentials/verify', TOKEN, { method: 'POST', body: JSON.stringify({ credential }) }); setCredentialResult(r.scope); })}>{t('Verify signature and current revocation', '核實簽署及現行撤銷狀態')}</Button>
        {credentialResult && <p role="status">{credentialResult}</p>}
      </section>
    </div>
  </details>;
}
