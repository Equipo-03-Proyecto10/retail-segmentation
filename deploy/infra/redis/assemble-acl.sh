#!/usr/bin/env bash
# Assemble the one aclfile both Redis instances load, then load it into both
# with ACL LOAD, with no restart (ADR-0037, ADR-0040).
#
#   deploy/infra/redis/assemble-acl.sh [--write-only]
#
# Every installed service commits services/<name>/redis/<name>.acl, one line:
#
#   user svc_<suffix> on {{password}} <rules>
#
# A deployable under ops/ (ADR-0055) commits ops/<name>/redis/<name>.acl the
# same way, as an `ops_<suffix>` user; its password key is REDIS_OPS_<SUFFIX>_PASSWORD.
#
# {{password}} becomes `#<sha256>` of REDIS_<SUFFIX>_PASSWORD, read from
# /etc/mosaiq/svc/<name>.env (<SUFFIX> is the svc_ role suffix, upper-cased).
# The file also gets the ops user, whose password is REDIS_OPS_PASSWORD from
# the environment, and the disabled default user. Without that line, ACL LOAD
# would recreate `default` with every permission and no password.
#
# ACL LOAD is all or nothing on each instance. When either instance refuses
# the new file, the previous file is put back, and redis-state is reloaded
# from it if it had already taken the new one. Both instances therefore keep
# their previous users, and the script exits 1. --write-only writes the file
# and loads nothing: the first start, before the instances run.
#
# Environment (each default is the instance's):
#   ACL_SOURCES       globs of user lines     services/*/redis/*.acl ops/*/redis/*.acl
#   SVC_ENV_DIR       per-service env files   /etc/mosaiq/svc
#   ACL_FILE          the assembled aclfile   /etc/mosaiq/redis/users.acl
#   REDIS_OPS_PASSWORD                         required
#   REDIS_STATE_CLI, REDIS_CACHE_CLI           a redis-cli signed in as ops;
#                                             the default runs it in the container
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
compose=(docker compose -p mosaiq-infra -f "$here/../compose.yaml" exec -T -e REDISCLI_AUTH)
ACL_SOURCES=${ACL_SOURCES:-services/*/redis/*.acl ops/*/redis/*.acl}
SVC_ENV_DIR=${SVC_ENV_DIR:-/etc/mosaiq/svc}
ACL_FILE=${ACL_FILE:-/etc/mosaiq/redis/users.acl}
read -r -a state_cli <<<"${REDIS_STATE_CLI:-${compose[*]} redis-state redis-cli --no-auth-warning --user ops}"
read -r -a cache_cli <<<"${REDIS_CACHE_CLI:-${compose[*]} redis-cache redis-cli --no-auth-warning --user ops}"

write_only=0
case ${1:-} in
  --write-only) write_only=1 ;;
  "") ;;
  *) echo "usage: $0 [--write-only]" >&2; exit 2 ;;
esac

die() { echo "assemble-acl: $*" >&2; exit 1; }

[[ -n ${REDIS_OPS_PASSWORD:-} ]] || die "REDIS_OPS_PASSWORD is not set"
# redis-cli reads the password from here, so it never appears in an argv.
export REDISCLI_AUTH=$REDIS_OPS_PASSWORD

sha256() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }

# One value from a KEY=VALUE file, never sourced: the file is data.
env_value() {
  local file=$1 key=$2 line value
  while IFS= read -r line || [[ -n $line ]]; do
    line=${line#export }
    [[ $line == "$key="* ]] || continue
    value=${line#"$key="}
    if [[ ${#value} -ge 2 && ${value:0:1} == "${value: -1}" && ${value:0:1} =~ [\"\'] ]]; then
      value=${value:1:-1}
    fi
    printf '%s' "$value"
    return 0
  done <"$file"
  return 1
}

lines=()
users=()
shopt -s nullglob
# shellcheck disable=SC2206  # the glob is meant to expand
sources=($ACL_SOURCES)
for source in "${sources[@]}"; do
  name=$(basename "$source" .acl)
  mapfile -t content < <(grep -vE '^[[:space:]]*(#|$)' "$source" || true)
  [[ ${#content[@]} -eq 1 ]] || die "$source: expected one user line, found ${#content[@]}"
  line=${content[0]}
  [[ $line =~ ^user\ ((svc|ops)_[a-z][a-z0-9_]*)\ on\ \{\{password\}\}\  ]] \
    || die "$source: must start with 'user svc_<suffix> on {{password}} ' (or ops_<suffix>)"
  user=${BASH_REMATCH[1]}
  [[ $(grep -o '{{password}}' <<<"$line" | wc -l) -eq 1 ]] || die "$source: {{password}} must appear once"
  # A committed line never carries a credential of its own.
  if grep -qE '(^| )(>|<|#|nopass|allkeys|allcommands|allchannels|resetpass)' <<<"${line/\{\{password\}\}/}"; then
    die "$source: carries a password, a hash or an all-access rule of its own"
  fi
  for seen in "${users[@]}"; do
    [[ $seen != "$user" ]] || die "$source: $user is defined twice"
  done
  suffix=${user#svc_}
  key="REDIS_${suffix^^}_PASSWORD"
  env_file="$SVC_ENV_DIR/$name.env"
  [[ -r $env_file ]] || die "$source: cannot read $env_file"
  password=$(env_value "$env_file" "$key") || die "$env_file: no $key"
  [[ -n $password ]] || die "$env_file: $key is empty"
  users+=("$user")
  lines+=("${line/\{\{password\}\}/#$(sha256 "$password")}")
done

dir=$(dirname "$ACL_FILE")
mkdir -p "$dir"
# One deploy at a time edits the shared aclfile (ADR-0040).
exec 9>"$ACL_FILE.lock"
flock 9

candidate=$(mktemp "$dir/.users.acl.XXXXXX")
trap 'rm -f "$candidate"' EXIT
{
  # The aclfile format has no comments; each line must be a user.
  echo "user default off resetkeys resetchannels -@all"
  echo "user ops on #$(sha256 "$REDIS_OPS_PASSWORD") ~* &* +@all -@dangerous +acl +config|get +info +client|list +slowlog +latency +monitor"
  printf '%s\n' "${lines[@]}" | LC_ALL=C sort
} >"$candidate"
# Both containers read it as their own redis user; it holds SHA-256 hashes of
# random passwords and nothing else.
chmod 0644 "$candidate"

if ((write_only)); then
  mv -f "$candidate" "$ACL_FILE"
  exit 0
fi

load() {
  local -n cli=$1
  local reply
  reply=$("${cli[@]}" ACL LOAD 2>&1) || true
  [[ $reply == OK ]] && return 0
  echo "assemble-acl: ACL LOAD on $2 refused the file: $reply" >&2
  return 1
}

previous=
if [[ -f $ACL_FILE ]]; then
  previous=$(mktemp "$dir/.users.acl.previous.XXXXXX")
  cp -p "$ACL_FILE" "$previous"
fi
restore() {
  if [[ -n $previous ]]; then mv -f "$previous" "$ACL_FILE"; else rm -f "$ACL_FILE"; fi
}

mv -f "$candidate" "$ACL_FILE"
if ! load state_cli redis-state; then
  restore
  exit 1
fi
if ! load cache_cli redis-cache; then
  restore
  load state_cli redis-state || echo "assemble-acl: redis-state could not reload the previous file" >&2
  exit 1
fi
[[ -n $previous ]] && rm -f "$previous"
echo "loaded ${#lines[@]} service users into redis-state and redis-cache"
