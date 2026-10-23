#!/usr/bin/env bash
# Make the local key pair auth-service signs with on a laptop (F14-02).
#
#   bash docker/distributed/auth-keys.sh        # once, before `docker compose up`
#
# Writes docker/distributed/keys/auth-local-dev.pem (the private key) and
# docker/distributed/keys/jwks.json (its public half), which local.env mounts
# in every service. Both are git-ignored (docker/distributed/keys/.gitignore)
# and the key is throwaway: it exists on this machine only and signs nothing
# else. The private file is world-readable so the container's UID 10001 can
# read the bind mount; the instance keeps the real one at 0400
# (deploy/auth-keys.md). Re-running keeps an existing pair.
set -euo pipefail

kid=local-dev
dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/keys
private=$dir/auth-$kid.pem

mkdir -p "$dir"
if [[ ! -f $private ]]; then
  (umask 077 && openssl genrsa -out "$private" 2048 2>/dev/null)
  chmod 0444 "$private"
fi
python3 "$dir/../../../tests/conformance/tokens.py" jwks "$private" "$kid" >"$dir/jwks.json"
chmod 0444 "$dir/jwks.json"
echo "auth-keys: $dir/jwks.json holds kid $kid"
