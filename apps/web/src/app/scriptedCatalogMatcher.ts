import type { Product } from '../../../../contracts/types';

export type ScriptedBasketLine = { product_id: string; quantity: number };
export type ScriptedMatchResult =
  | { ok: true; items: ScriptedBasketLine[] }
  | { ok: false; message: string };

function normalizeTitle(value: string) {
  return value.normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
}

function parseLine(line: string) {
  const prefixedQuantity = line.match(/^\s*(\d+)\s*[x×]\s+(.+?)\s*$/i);
  if (prefixedQuantity) return { quantity: Number(prefixedQuantity[1]), title: prefixedQuantity[2] };
  const suffixedQuantity = line.match(/^\s*(.+?)\s+[x×]\s*(\d+)\s*$/i);
  if (suffixedQuantity) return { quantity: Number(suffixedQuantity[2]), title: suffixedQuantity[1] };
  return { quantity: 1, title: line.trim() };
}

/**
 * Explicitly scripted demo fallback: match complete catalog titles only.
 * It intentionally does not guess, use an LLM, or select an approximate item.
 */
export function matchScriptedCatalogBasket(
  lines: string[],
  products: Product[],
  merchantId: string,
): ScriptedMatchResult {
  if (lines.length === 0) return { ok: false, message: 'Enter at least one catalog product title.' };

  const available = products.filter((product) => product.merchant_id === merchantId && product.available);
  const selected = new Map<string, ScriptedBasketLine>();
  for (const rawLine of lines) {
    const { quantity, title } = parseLine(rawLine);
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 20) {
      return { ok: false, message: `Quantity for “${title}” must be between 1 and 20.` };
    }
    const key = normalizeTitle(title);
    if (!key) return { ok: false, message: 'Enter a product title on every non-empty line.' };
    const matches = available.filter((product) => normalizeTitle(product.title) === key);
    if (matches.length === 0) {
      return {
        ok: false,
        message: `Could not find an exact available catalog title for “${title}”. Search the catalog and select the product manually.`,
      };
    }
    if (matches.length > 1) {
      return { ok: false, message: `“${title}” matches more than one catalog item. Select the product manually.` };
    }
    const product = matches[0];
    const nextQuantity = (selected.get(product.id)?.quantity ?? 0) + quantity;
    if (nextQuantity > 20) return { ok: false, message: `The combined quantity for “${product.title}” exceeds 20.` };
    selected.set(product.id, { product_id: product.id, quantity: nextQuantity });
  }
  return { ok: true, items: [...selected.values()] };
}
