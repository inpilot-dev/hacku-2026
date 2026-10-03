"""Join per-shop catalog files into the single file the wallet loads.

    python -m catalog_capture.combine data/catalog/wellcome.json data/catalog/marketplace.json \
        --out data/catalog/stores.json

The wallet's Catalog reads one file (MANDATE_CATALOG_PATH). Each input keeps its
own merchant, products, delivery contexts and evidence; an ID that appears in two
inputs aborts the join rather than letting one shop's record replace another's.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class CombineError(Exception):
    pass


def combine(catalogs: list[dict]) -> dict:
    out = {"_note": "Combined observed snapshots: " + " | ".join(c.get("_note", "") for c in catalogs),
           "merchants": {}, "products": [], "delivery_contexts": [], "evidence": []}
    seen: dict[str, set] = {key: set() for key in ("products", "delivery_contexts", "evidence")}
    for catalog in catalogs:
        for merchant_id, merchant in catalog["merchants"].items():
            if merchant_id in out["merchants"]:
                raise CombineError(f"Merchant {merchant_id} appears in two inputs.")
            out["merchants"][merchant_id] = merchant
        for key in seen:
            for record in catalog[key]:
                if record["id"] in seen[key]:
                    raise CombineError(f"{key} id {record['id']} appears in two inputs.")
                seen[key].add(record["id"])
                out[key].append(record)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Join per-shop catalogs into one wallet catalog file.")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        joined = combine([json.loads(p.read_text()) for p in args.inputs])
    except CombineError as exc:
        print(f"Not combined: {exc}", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(joined, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(joined['products'])} products from {len(joined['merchants'])} shops to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
