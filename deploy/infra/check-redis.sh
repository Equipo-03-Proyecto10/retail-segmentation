#!/usr/bin/env bash
# ADR-0037's checks against the two running Redis instances, as the ops user.
# Prints nothing when they hold; each problem is one "FAIL:" line, and the
# exit status is 1 when there is any.
#
#   deploy/infra/check-redis.sh
#
# Per user, by ACL DRYRUN, so nothing is written:
#   - every svc_* user but svc_auth may EXISTS the liveness family and nothing
#     else on it, and every svc_* user is denied auth's keys, another
#     service's prefix, SCAN, KEYS, RANDOMKEY, FLUSHALL and EVAL;
#   - svc_auth alone may MULTI;
#   - only a stream's producer may XADD to it, as named by
#     contracts/streams/<producer>.<event>.v1.md.
# Per instance:
#   - the memory, eviction and persistence settings of ADR-0037;
#   - the default user is off, so a client without credentials gets NOAUTH;
#   - both instances hold the same users;
#   - every non-stream key on redis-state has a TTL, and no cache key lives
#     there.
#
# Environment:
#   REDIS_OPS_PASSWORD                    required
#   REDIS_STATE_CLI, REDIS_CACHE_CLI      a redis-cli signed in as ops
#   REDIS_STATE_ANON_CLI, REDIS_CACHE_ANON_CLI
#                                         the same, with no credentials
#   STREAMS_DIR                           contracts/streams
# The defaults run redis-cli inside the mosaiq-infra containers.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
exec_in=(docker compose -p mosaiq-infra -f "$here/compose.yaml" exec -T)
read -r -a state_cli <<<"${REDIS_STATE_CLI:-${exec_in[*]} -e REDISCLI_AUTH redis-state redis-cli --no-auth-warning --user ops}"
read -r -a cache_cli <<<"${REDIS_CACHE_CLI:-${exec_in[*]} -e REDISCLI_AUTH redis-cache redis-cli --no-auth-warning --user ops}"
read -r -a state_anon <<<"${REDIS_STATE_ANON_CLI:-${exec_in[*]} redis-state redis-cli}"
read -r -a cache_anon <<<"${REDIS_CACHE_ANON_CLI:-${exec_in[*]} redis-cache redis-cli}"
STREAMS_DIR=${STREAMS_DIR:-contracts/streams}

if [[ -z ${REDIS_OPS_PASSWORD:-} ]]; then
  echo "FAIL: REDIS_OPS_PASSWORD is not set"
  exit 1
fi
# redis-cli reads the password from here, so it never appears in an argv.
export REDISCLI_AUTH=$REDIS_OPS_PASSWORD

failed=0
fail() {
  echo "FAIL: $*"
  failed=1
}

# --- settings -----------------------------------------------------------------
setting() {
  local -n cli=$1
  "${cli[@]}" CONFIG GET "$2" 2>&1 | sed -n 2p
}
expect_setting() {
  local actual
  actual=$(setting "$1" "$3")
  [[ $actual == "$4" ]] || fail "$2 $3 is '$actual', expected '$4'"
}
expect_setting state_cli redis-state maxmemory 67108864
expect_setting state_cli redis-state maxmemory-policy noeviction
expect_setting state_cli redis-state appendonly yes
expect_setting state_cli redis-state appendfsync everysec
expect_setting state_cli redis-state save ""
expect_setting cache_cli redis-cache maxmemory 134217728
expect_setting cache_cli redis-cache maxmemory-policy allkeys-lru
expect_setting cache_cli redis-cache appendonly no
expect_setting cache_cli redis-cache save ""

# --- the default user -----------------------------------------------------------
for instance in redis-state redis-cache; do
  if [[ $instance == redis-state ]]; then
    reply=$(env -u REDISCLI_AUTH "${state_anon[@]}" PING 2>&1)
  else
    reply=$(env -u REDISCLI_AUTH "${cache_anon[@]}" PING 2>&1)
  fi
  [[ $reply == *NOAUTH* || $reply == *WRONGPASS* ]] \
    || fail "$instance answers a client without credentials: '$reply'"
done
# `default` keeps every permission so the AOF replay can re-run auth's MULTI/EXEC
# blocks (ADR-0065). That is safe only while it is off and has no password.
for instance in redis-state redis-cache; do
  if [[ $instance == redis-state ]]; then
    line=$("${state_cli[@]}" ACL LIST 2>&1 | grep '^user default ' || true)
  else
    line=$("${cache_cli[@]}" ACL LIST 2>&1 | grep '^user default ' || true)
  fi
  [[ $line == "user default off "* ]] || fail "$instance: the default user is not off: '$line'"
  [[ $line != *" nopass"* && $line != *" #"* && $line != *" >"* ]] \
    || fail "$instance: the default user has a password or nopass: '$line'"
  [[ $line == *"~* "* && $line == *"&* "* && $line == *"+@all"* ]] \
    || fail "$instance: the default user is not the AOF-replay line of ADR-0065: '$line'"
done

# --- the same users on both instances ------------------------------------------
state_users=$("${state_cli[@]}" ACL LIST 2>&1)
cache_users=$("${cache_cli[@]}" ACL LIST 2>&1)
[[ $state_users == "$cache_users" ]] || fail "redis-state and redis-cache hold different users"
mapfile -t services < <("${state_cli[@]}" ACL USERS 2>&1 | grep -E '^svc_' | LC_ALL=C sort)

# --- per user, by ACL DRYRUN ----------------------------------------------------
# One redis-cli reads every DRYRUN from stdin, one reply per line, so a check
# over docker exec costs one round trip, not one per command.
checks=()
expect=()
labels=()
add() { # user, OK|DENIED, command...
  local user=$1 want=$2
  shift 2
  checks+=("ACL DRYRUN $user $*")
  expect+=("$want")
  labels+=("$user: $*")
}

streams=()
shopt -s nullglob
for contract in "$STREAMS_DIR"/*.v1.md; do
  base=$(basename "$contract")
  [[ $base =~ ^([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)\.v1\.md$ ]] || continue
  streams+=("${BASH_REMATCH[1]} stream:${BASH_REMATCH[1]}.v1.${BASH_REMATCH[2]}")
done

for user in "${services[@]}"; do
  if [[ $user != svc_auth ]]; then
    add "$user" OK EXISTS pub:auth:v1:live:x
    add "$user" DENIED DEL pub:auth:v1:live:x
    add "$user" DENIED HGETALL auth:session:x
    add "$user" DENIED GET auth:refresh:x
    add "$user" DENIED MULTI
  else
    add "$user" OK MULTI
  fi
  add "$user" DENIED SET other:rl:x 1
  add "$user" DENIED SCAN 0
  add "$user" DENIED KEYS '"*"'
  add "$user" DENIED RANDOMKEY
  add "$user" DENIED FLUSHALL
  add "$user" DENIED EVAL '"return 1"' 0
  for entry in "${streams[@]}"; do
    producer=${entry%% *}
    stream=${entry#* }
    if [[ $user == "svc_$producer" ]]; then
      add "$user" OK XADD "$stream" '*' f v
    else
      add "$user" DENIED XADD "$stream" '*' f v
    fi
  done
done

if ((${#checks[@]})); then
  mapfile -t replies < <(printf '%s\n' "${checks[@]}" | "${state_cli[@]}" 2>&1)
  if [[ ${#replies[@]} -ne ${#checks[@]} ]]; then
    fail "expected ${#checks[@]} ACL DRYRUN replies from redis-state, got ${#replies[@]}"
  else
    for i in "${!checks[@]}"; do
      if [[ ${expect[$i]} == OK && ${replies[$i]} != OK ]]; then
        fail "${labels[$i]} is refused: ${replies[$i]}"
      elif [[ ${expect[$i]} == DENIED && ${replies[$i]} == OK ]]; then
        fail "${labels[$i]} is allowed"
      fi
    done
  fi
fi

# --- keys on redis-state --------------------------------------------------------
# A key without a TTL on a noeviction instance grows until writes fail; a cache
# key belongs on redis-cache. Streams are capped by length instead.
mapfile -t keys < <("${state_cli[@]}" --scan 2>&1)
if ((${#keys[@]})); then
  quoted=()
  for key in "${keys[@]}"; do
    escaped=${key//\\/\\\\}
    escaped=${escaped//\"/\\\"}
    quoted+=("\"$escaped\"")
  done
  mapfile -t kinds < <(for q in "${quoted[@]}"; do printf 'TYPE %s\nTTL %s\n' "$q" "$q"; done \
    | "${state_cli[@]}" 2>&1)
  for i in "${!keys[@]}"; do
    kind=${kinds[$((2 * i))]:-}
    ttl=${kinds[$((2 * i + 1))]:-}
    [[ ${keys[$i]} == *:cache:* ]] && fail "cache key on redis-state: ${keys[$i]}"
    [[ $kind != stream && $ttl == -1 ]] && fail "key without a TTL on redis-state: ${keys[$i]}"
  done
fi

exit "$failed"
