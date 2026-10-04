import { useLocale } from '@/lib/locale';
import { Check, ChevronRight, Printer, ReceiptText } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { money } from '@/lib/format';
import { useAccount, type ReceiptRecord } from '../data/account';
import { storeName } from './AllowanceSetup';

/* Paid grocery receipts from the wallet's audit trail, and one receipt to read or print. */

const when = (r: ReceiptRecord, style: 'short' | 'long' = 'short') => new Date(r.receipt.paid_at || r.occurredAt)
  .toLocaleString('en-HK', style === 'long' ? { dateStyle: 'long', timeStyle: 'short' } : { dateStyle: 'medium', timeStyle: 'short' });

export function ReceiptList({ limit }: { limit?: number }) {
  const { receipts } = useAccount();
  const { t } = useLocale();
  if (receipts.loading && !receipts.records.length) return <div className="space-y-2"><Skeleton className="h-14" /><Skeleton className="h-14" /></div>;
  if (receipts.error) return <div className="space-y-2 text-sm"><p className="text-destructive" role="alert">{receipts.error}</p>
    <Button variant="outline" size="sm" onClick={() => void receipts.load(true)}>{t('Try again', '再試一次')}</Button></div>;
  if (!receipts.records.length) return <p className="text-sm text-muted-foreground">{t('No paid receipts yet. Refused orders are not receipts.', '暫未有已付款收據。被拒絕的訂單不會產生收據。')}</p>;
  const shown = limit ? receipts.records.slice(0, limit) : receipts.records;
  const latest = shown[0];
  const label = (record: ReceiptRecord) => `${storeName(record.receipt.merchant_id)} groceries ${when(record)} ${money(record.receipt.amount_minor)}`;
  return <div className="receipt-collection">
    <button className="receipt-preview" onClick={() => void receipts.open(latest)} aria-label={label(latest)}>
      <span className="receipt-preview-top"><span>{t('Latest receipt', '最新收據')}</span><Check className="size-3.5" aria-hidden="true" /></span>
      <span className="receipt-preview-main"><strong>{storeName(latest.receipt.merchant_id)}</strong><strong>{money(latest.receipt.amount_minor)}</strong></span>
      <span className="receipt-preview-bottom"><time>{when(latest)}</time><span>{t('View receipt', '查看收據')}<ChevronRight className="size-3.5" aria-hidden="true" /></span></span>
    </button>
    {shown.length > 1 && <ul className="receipt-list">
      {shown.slice(1).map((record) => <li key={record.receipt.transaction_id}>
        <button className="receipt-list-row" onClick={() => void receipts.open(record)} aria-label={label(record)}>
          <span className="receipt-list-icon"><ReceiptText className="size-4" aria-hidden="true" /></span>
          <span className="receipt-list-copy"><strong>{storeName(record.receipt.merchant_id)}</strong><time>{when(record)}</time></span>
          <span className="receipt-list-amount">{money(record.receipt.amount_minor)}</span>
          <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
        </button>
      </li>)}
    </ul>}
  </div>;
}

export function ReceiptDialog() {
  const { receipts } = useAccount();
  const { t } = useLocale();
  const record = receipts.selected;
  return <Dialog open={Boolean(record)} onOpenChange={(open) => { if (!open) receipts.close(); }}>
    <DialogContent className="receipt-dialog max-h-[92dvh] overflow-y-auto gap-0 p-0 sm:max-w-md">
      {record && <>
        <DialogHeader className="receipt-toolbar px-6 pt-5 pb-4">
          <DialogTitle className="flex items-center gap-2 text-sm"><ReceiptText className="size-4 text-stella" />{t("Receipt", "收據")}</DialogTitle>
          <DialogDescription className="sr-only">{storeName(record.receipt.merchant_id)} · {when(record, 'long')}</DialogDescription>
        </DialogHeader>
        <div className="receipt-stage">
          <article id="printable-receipt" className="receipt-sheet">
            <header className="receipt-head">
              <span className="receipt-brand">MANDATE</span>
              <h2>{storeName(record.receipt.merchant_id)}</h2>
              <p>{t('GROCERY RECEIPT', '日用品收據')}</p>
              <time>{when(record, 'long')}</time>
              <span className="receipt-stamp"><Check aria-hidden="true" /> {t("PAID", "已付款")}</span>
            </header>
            <div className="receipt-rule" />
            <div className="receipt-columns"><span>{t('ITEM / QTY', '商品／數量')}</span><span>HKD</span></div>
            {receipts.detailLoading ? <Skeleton className="my-4 h-24" /> : record.quote ? <>
              <ul className="receipt-items">
                {record.quote.items.map((item) => <li key={item.product_id}>
                  <div className="receipt-item-name">{item.title}</div>
                  <div className="receipt-item-amount"><span>QTY {item.quantity}</span><span>{money(item.line_total_minor)}</span></div>
                </li>)}
              </ul>
              {record.quote.charges.length > 0 && <div className="receipt-charges">
                {record.quote.charges.map((c, i) => <div key={`${c.label}-${i}`}><span>{c.label}</span><span>{c.amount_minor ? money(c.amount_minor) : 'Free'}</span></div>)}
              </div>}
            </> : <p className="receipt-unavailable">{receipts.detailError || 'Item details are no longer available for this purchase.'}</p>}
            <div className="receipt-rule" />
            <div className="receipt-total"><span>{t('TOTAL PAID', '已支付總額')}</span><strong>{money(record.receipt.amount_minor)}</strong></div>
            <div className="receipt-payment"><span>{t('Payment', '付款')}</span><span>{record.receipt.payment_route?.label.replace(/\s*\(scoped network token\)/gi, '') ?? 'Sandbox wallet'}</span></div>
            <div className="receipt-rule" />
            <dl className="receipt-reference">
              <dt>{t('Receipt ID', '收據編號')}</dt><dd>{record.receipt.id}</dd>
              <dt>{t('Transaction ID', '交易編號')}</dt><dd>{record.receipt.transaction_id}</dd>
            </dl>
            <footer className="receipt-foot"><span>{t('Sandbox payment', '沙盒付款')}</span><span>Kept by Stella · Mandate</span></footer>
          </article>
        </div>
        <div className="receipt-actions px-6 pt-3 pb-5 no-print">
          <Button variant="outline" className="w-full bg-background" onClick={() => window.print()}><Printer />{t('Print or save PDF', '列印或儲存 PDF')}</Button>
        </div>
      </>}

    </DialogContent>
  </Dialog>;
}
