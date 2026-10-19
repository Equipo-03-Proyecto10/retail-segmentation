#!/usr/bin/env bash
# compose.distributed.yaml's one-shot mongo-init step: run each
# services/*/mongo/init.js against the local mongodb as root, once per version
# of the file (ADR-0038). The instance applies the same file by hand before
# the service version that needs it (deploy/infra/README.md).
#
# Root's credentials and every MONGO_<SUFFIX>_PASSWORD arrive in the
# environment; init.js reads its password from process.env.
set -euo pipefail
shopt -s nullglob

uri=mongodb://mongodb:27017/admin
auth='db.getSiblingDB("admin").auth(process.env.MONGO_INITDB_ROOT_USERNAME, process.env.MONGO_INITDB_ROOT_PASSWORD)'

for _ in $(seq 1 60); do
  mongosh "$uri" --quiet --norc --eval "$auth" >/dev/null 2>&1 && break
  sleep 2
done
mongosh "$uri" --quiet --norc --eval "$auth" >/dev/null || { echo "mongo-init: mongodb did not accept root" >&2; exit 1; }

for file in /repo/services/*/mongo/init.js; do
  name=$(basename "$(dirname "$(dirname "$file")")")
  digest=$(sha256sum "$file" | cut -d' ' -f1)
  if [[ -f /state/$name.sha256 && $(cat "/state/$name.sha256") == "$digest" ]]; then
    echo "mongo-init: $name unchanged"
    continue
  fi
  printf '%s;\nload(%s);\n' "$auth" "\"$file\"" >/tmp/run.js
  mongosh "$uri" --quiet --norc /tmp/run.js
  echo "$digest" >"/state/$name.sha256"
  echo "mongo-init: applied $file"
done
