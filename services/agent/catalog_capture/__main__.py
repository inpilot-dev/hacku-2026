"""Capture an observed Wellcome catalog: python -m catalog_capture [--out DIR]."""

import argparse
import sys
from pathlib import Path

from .capture import CaptureError, Run

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "catalog",
                        help="Catalog directory inside the repository (default: data/catalog).")
    args = parser.parse_args()
    run = Run(REPO_ROOT, args.out)
    try:
        catalog = run.build()
    except CaptureError as exc:
        print(f"Capture failed, nothing written: {exc}", file=sys.stderr)
        return 1
    target = run.write(catalog)
    print(f"Wrote {len(catalog['products'])} products and {len(run.pages)} evidence pages to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
