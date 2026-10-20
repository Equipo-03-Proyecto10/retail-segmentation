#!/usr/bin/env bash
# The repository-level rules of the distributed stage, as greps over tracked
# files (F13-09). Prints nothing, and exits 0, when every rule holds; prints
# one line per problem and exits 1 otherwise. Run from the repository root.
#
# Each block names the record whose Compliance section it carries. Service
# names come from contracts/registry.yaml; this file is the one place under
# deploy/ and .github/ allowed to name a service (the golden-fixture list).
set -uo pipefail
shopt -s nullglob

problems=$(
  # Lines that git grep matched, each prefixed with the record they break.
  found() { sed "s|^|$1: |"; }

  # ADR-0035 and ADR-0040: NGINX answers its own 413 with the shared envelope.
  for c in services/*/nginx/*.conf; do
    n=$(basename "$c" .conf)
    grep -qF "error_page 413 @mosaiq_${n}_413_\$mosaiq_error_format;" "$c" \
      && grep -qF "location @mosaiq_${n}_413_json {" "$c" \
      && grep -qF "location @mosaiq_${n}_413_xml {" "$c" \
      || echo "ADR-0035: $c does not answer 413 through @mosaiq_${n}_413_json and _xml"
  done

  # ADR-0045: services never import, copy or depend on the web or each other.
  git grep -nE '^\s*(from|import)\s+web(\.|\s|$)' -- services/ packages/ bench/ | found ADR-0045
  git grep -niE '^\s*(COPY|ADD)\s.*(\bweb/|\.\./)' -- 'services/*/Dockerfile' 'bench/*/Dockerfile' | found ADR-0045
  for f in services/*/compose.yaml; do
    yq -r '.services[] | select(has("build")) | (.build.context // .build)' "$f" \
      | grep -vxE '\.|\./' | sed "s|^|ADR-0045: $f builds from |"
  done
  git grep -nE 'path\s*=\s*"[^"]*packages/|file://|\.\./packages' \
    -- 'services/*/pyproject.toml' 'services/*/Cargo.toml' 'bench/*/Cargo.toml' | found ADR-0045
  for f in services/*/pyproject.toml services/*/Cargo.toml; do
    grep -qE 'kit-(py|rs)/v[0-9]+\.[0-9]+\.[0-9]+' "$f" || echo "ADR-0045: $f does not pin a kit by exact tag"
  done
  git grep -lE '^\[workspace\]' -- Cargo.toml 'services/*/Cargo.toml' 'bench/*/Cargo.toml' | found ADR-0045
  for d in services/*/; do
    d=${d%/}
    [ -f "$d/VERSION" ] || echo "ADR-0045: $d has no VERSION"
    locks=$(find "$d" -maxdepth 1 \( -name uv.lock -o -name Cargo.lock \) | wc -l)
    [ "$locks" -eq 1 ] || echo "ADR-0045: $d has $locks lock files"
  done
  git grep -nwE 'ADMIN|ANALYST|STORE_MANAGER|MARKETING|INVENTORY_PLANNER|AUDITOR|CUSTOMER' -- packages/ \
    | found "ADR-0045 role code in a kit"
  git grep -noE "[\"'][a-z_]+:[a-z_]+:[^\"']*" -- packages/ \
    | grep -vE "pub:auth:v1:live:|[\"']stream:" | found "ADR-0045 Redis key in a kit"
  for s in sales recommendations users campaigns; do
    if [ -d "services/$s" ] && [ -z "$(git ls-files "services/$s/tests/golden/*.json")" ]; then
      echo "ADR-0045: services/$s has no golden fixtures in tests/golden/"
    fi
  done

  # ADR-0036: only auth signs, RS256 only, a role and no permissions. A
  # service's own tests sign with a throwaway key, as the conformance suite
  # does: the kit cannot ship real role codes (ADR-0045), so no pre-signed
  # vector carries one. Those tests never reach an image.
  git grep -nE 'jwt\.encode|EncodingKey::from' -- services/ packages/ bench/ \
    ':(exclude)services/*/tests/*' ':(exclude)bench/*/tests/*' \
    | grep -v '^services/auth/' | found "ADR-0036 signing outside auth"
  git grep -nE 'HS256|HS384|HS512|JWT_SECRET' -- services/ packages/ bench/ contracts/ .env.example \
    | found ADR-0036
  git grep -nE '"permissions"|\bpermissions\s*:' -- contracts/auth/ services/auth/ | found ADR-0036
  git grep -n password_hash -- contracts/users/ | found ADR-0036
  git grep -nE 'algorithms\s*=\s*\[' -- packages/kit-py | grep -v '"RS256"\]' | found ADR-0036
  git grep -n 'Validation::new(' -- packages/kit-rs | grep -v 'Algorithm::RS256' | found ADR-0036
  git grep -q 'algorithms=\["RS256"\]' -- packages/kit-py/src || echo "ADR-0036: kit-py does not pin RS256"
  git grep -q 'Validation::new(Algorithm::RS256)' -- packages/kit-rs/src || echo "ADR-0036: kit-rs does not pin RS256"

  # ADR-0038: MongoDB belongs to engagement alone, and nothing is deleted.
  git grep -lE 'MONGO_URI|pymongo|motor|mongodb' -- services/ packages/ bench/ \
    | grep -vE '^services/engagement/' | found "ADR-0038 MongoDB named outside engagement"
  git grep -nE '^\s*mongo(db)?:' -- 'services/*/compose.yaml' | found ADR-0038
  git grep -nE '\b(delete_one|delete_many|find_one_and_delete|drop_collection|drop_database|collMod|createUser)\b|\.drop\(' \
    -- services/engagement/src/ | found ADR-0038

  # ADR-0044: no other boundary's outbox channel, and no XADD but the kit's.
  for d in services/*/; do
    d=${d%/}
    n=$(basename "$d")
    own=$(sed -n 's/^user svc_\([a-z][a-z0-9_]*\) .*/\1/p' "$d/redis/$n.acl" 2>/dev/null)
    git grep -hoE '\b[a-z_]+_outbox\b' -- "$d" ":(exclude)$d/tests/" | sort -u \
      | grep -vx "${own:-${n//-/_}}_outbox" | sed "s|^|ADR-0044: $n names |"
  done
  git grep -niE '\bxadd\b' -- 'services/*.py' 'services/*.rs' 'bench/*.py' 'bench/*.rs' \
    ':(exclude)services/*/tests/*' ':(exclude)bench/*/tests/*' | found "ADR-0044 direct XADD"

  # ADR-0046: no DDL outside sql/01_schema.sql.
  git grep -niE '^\s*(create|alter|drop)\s+(table|schema)\b' -- services/ packages/ bench/ deploy/ \
    | found ADR-0046

  # ADR-0040: each project's compose and NGINX include, and nothing in
  # deploy/ or .github/ names a service.
  bash deploy/templates/service/check.sh services | sed 's|^FAIL: |ADR-0040: |'
  names=$(sed -n 's/^  - name: //p' contracts/registry.yaml | paste -sd'|')
  git grep -nE "mosaiq-($names)\b" -- .github/ deploy/ ':(exclude).github/scripts/guards.sh' \
    | found "ADR-0040 a service named"

  # ADR-0037: Redis runs in mosaiq-infra only, pinned, and the web never names it.
  git grep -nE 'image:\s*redis' -- 'services/*/compose.yaml' | found "ADR-0037 a service runs Redis"
  git grep -niwE 'redis' -- web/ | found "ADR-0037 the web names Redis"
  pinned=$(grep -cE 'image: redis:7\.4\.[0-9]+(-[a-z]+)?@sha256:[0-9a-f]{64}' deploy/infra/compose.yaml)
  [ "$pinned" -eq 2 ] || echo "ADR-0037: $pinned of 2 Redis images pinned by digest in deploy/infra/compose.yaml"
)

if [ -n "$problems" ]; then
  printf '%s\n' "$problems"
  exit 1
fi
