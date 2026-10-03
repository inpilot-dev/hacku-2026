#!/usr/bin/env bash
# Start the independent audit verifier as its own process.
# Creates the checkpoint signing key if missing, then pins its public key on the
# verifier's first run. Usage: start-verifier.sh <python> <audit-key-dir> <verifier-data-dir> [port]
set -euo pipefail

python_bin="$1" key_dir="$2" data_dir="$3" port="${4:-8201}"
cd "$(dirname "${BASH_SOURCE[0]}")/../services/api"
"$python_bin" -c "import sys; from mandate.audit.checkpoints import CheckpointSigner; CheckpointSigner.from_key_dir(sys.argv[1])" "$key_dir"
exec "$python_bin" -m mandate.audit.verifier --port "$port" --data-dir "$data_dir" \
  --public-key "$key_dir/checkpoint_ed25519.pub.pem"
