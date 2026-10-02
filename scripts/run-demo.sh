#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_INDEX="$REPO_ROOT/apps/web/dist/index.html"

if [[ ! -f "$WEB_INDEX" ]]; then
  echo "Built frontend not found. Run 'npm install && npm run build' in apps/web first." >&2
  exit 1
fi

cleanup_ephemeral_data=0
if [[ -n "${MANDATE_DEMO_DATA_DIR:-}" ]]; then
  demo_data_dir="$MANDATE_DEMO_DATA_DIR"
  mkdir -p "$demo_data_dir"
  demo_data_dir="$(cd "$demo_data_dir" && pwd)"
else
  demo_data_dir="$(mktemp -d "${TMPDIR:-/tmp}/mandate-demo.XXXXXX")"
  cleanup_ephemeral_data=1
fi

cleanup() {
  if [[ "$cleanup_ephemeral_data" == 1 ]]; then
    rm -rf -- "$demo_data_dir"
  fi
}
trap cleanup EXIT

export MANDATE_ENABLE_DEMO_CHECKOUT=1
if [[ -z "${MANDATE_CATALOG_PATH:-}" ]]; then
  export MANDATE_CATALOG_PATH="$REPO_ROOT/data/catalog/wellcome.json"
fi
export MANDATE_WALLET_DATA_DIR="$demo_data_dir"
export MANDATE_WALLET_KEY_DIR="$demo_data_dir/keys"

demo_port="${MANDATE_DEMO_PORT:-8000}"
python_bin="${PYTHON_BIN:-python3}"
echo "Mandate demo: http://127.0.0.1:$demo_port/"
if [[ "$cleanup_ephemeral_data" == 1 ]]; then
  echo "Fresh temporary sandbox; its local ledger will be removed when this process exits."
else
  echo "Using persistent demo data at: $demo_data_dir"
fi

cd "$REPO_ROOT/services/api"
"$python_bin" -m uvicorn mandate.app:app --host 127.0.0.1 --port "$demo_port"
