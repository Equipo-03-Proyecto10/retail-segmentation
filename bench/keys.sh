#!/usr/bin/env bash
# The bench's throwaway key pair (ADR-0042): auth-service signs with it, every
# bench container verifies with its JWKS, and the conformance suite signs its
# own test tokens with it. Written to bench/keys/ (git-ignored); re-running
# keeps an existing pair. It is never the instance's key.
set -euo pipefail

kid=bench
dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/keys
private=$dir/auth-$kid.pem

mkdir -p "$dir"
if [[ ! -f $private ]]; then
  (umask 077 && openssl genrsa -out "$private" 2048 2>/dev/null)
  chmod 0444 "$private"
fi
python3 "$dir/../../tests/conformance/tokens.py" jwks "$private" "$kid" >"$dir/jwks.json"
chmod 0444 "$dir/jwks.json"
echo "bench-keys: $dir/jwks.json holds kid $kid"
