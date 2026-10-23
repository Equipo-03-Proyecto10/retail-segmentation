#!/usr/bin/env bash
# Deploy one released service, and roll back that service alone when any step
# fails. F18-03 (issue #456), ADR-0040.
#
#   deploy/deploy-service.sh <name> <version>
#
# Run as root on the instance. .github/workflows/deploy-service.yml copies the
# merged commit's deploy/, contracts/registry.yaml and services/<name>/ to a
# fresh directory there and runs that copy, so every file staged below comes
# from the merged commit. It never reads /opt/mosaiq/current, which
# deploy/deploy.sh owns, and it never touches the database: a release that
# needs a schema change has its boundary section applied before it merges
# (deploy/README.md, F18-03).
#
#   1. Refuse to start without /etc/mosaiq/svc/<name>.env, and record the
#      release `current` points to.
#   2. Stage /opt/mosaiq/services/<name>/releases/<version>/: compose.yaml,
#      nginx/, redis/, mongo/ when there is one, and release.env.
#   3. Load the service's ACL line into both Redis instances.       [host lock]
#   4. Pull the image, then `up -d --no-deps`.
#   5. Poll http://127.0.0.1:<port>/health/ready for READY_SECONDS.
#   6. Install the NGINX include, then `nginx -t` and a reload.     [host lock]
#   7. Point `current` at the new release.
#
# When a step from 3 on fails, what it and the steps before it changed is put
# back: the previous release's container (its image tag is still on disk), its
# ACL line and its include. That release's readiness is checked and the script
# exits 70; 71 means the rollback failed too and the service needs a person.
# Nothing of another service is touched. A failure in steps 1 and 2 changes
# nothing and exits 2.
#
# Step 3 records the line it loads in /opt/mosaiq/services/<name>/acl/ and
# builds the aclfile from every service's recorded line (its current release's
# before its first deploy here). Two deploys at once therefore never load each
# other's previous line.
#
# <version> is the image tag, services/<name>/VERSION. It may carry semver
# build metadata, `0.1.4+rehearsal`: a separate release of the same tag, which
# is how deploy/rehearse-rollback.sh stages a broken release with no new image.
#
# Environment (each default is the instance's):
#   SERVICES_ROOT   staged releases                 /opt/mosaiq/services
#   SVC_ENV_DIR     per-service secrets             /etc/mosaiq/svc
#   NGINX_API_DIR   installed includes              /etc/nginx/mosaiq-api.d
#   REDIS_OPS_ENV   holds REDIS_OPS_PASSWORD        /root/mosaiq-redis-ops.env
#   DEPLOY_LOCK     the host lock of steps 3 and 6  /run/lock/mosaiq-deploy.lock
#   READY_SECONDS   step 5's budget                 60
#   REQUIRE_ROOT    0 lets the tests run it unprivileged
# assemble-acl.sh's own variables (ACL_FILE, REDIS_STATE_CLI, ...) pass through.
set -euo pipefail

usage() {
  echo "usage: deploy-service.sh <name> <version>" >&2
  exit 64
}
[[ $# -eq 2 ]] || usage
name=$1
version=$2
[[ $name =~ ^[a-z][a-z0-9-]*$ ]] || usage
[[ $version =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(\+[0-9A-Za-z.-]+)?$ ]] || usage
tag=${version%%+*}

tree=$(cd "$(dirname "$0")/.." && pwd)
src=$tree/services/$name
SERVICES_ROOT=${SERVICES_ROOT:-/opt/mosaiq/services}
SVC_ENV_DIR=${SVC_ENV_DIR:-/etc/mosaiq/svc}
NGINX_API_DIR=${NGINX_API_DIR:-/etc/nginx/mosaiq-api.d}
REDIS_OPS_ENV=${REDIS_OPS_ENV:-/root/mosaiq-redis-ops.env}
DEPLOY_LOCK=${DEPLOY_LOCK:-/run/lock/mosaiq-deploy.lock}
READY_SECONDS=${READY_SECONDS:-60}
READY_INTERVAL=${READY_INTERVAL:-2}
LOCK_SECONDS=${LOCK_SECONDS:-600}

home=$SERVICES_ROOT/$name
rel=$home/releases/$version
env_file=$SVC_ENV_DIR/$name.env
compose=(docker compose -p mosaiq-"$name" --env-file "$rel/release.env" -f "$rel/compose.yaml")

say() { printf '\n=== %s\n' "$*"; }
# Before anything has changed.
die() {
  echo "deploy-service: $*; nothing was changed" >&2
  exit 2
}

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

release_tag() { sed -n 's/^IMAGE_TAG=//p' "$1/release.env" 2>/dev/null; }

relabel() {
  if command -v restorecon >/dev/null; then restorecon "$@"; fi
}

# Run "$@" holding the host lock that every service's deploy shares for the
# aclfile and NGINX. Closing the descriptor releases it.
with_lock() {
  local status=0
  exec 8>"$DEPLOY_LOCK" || return 1
  if ! flock -w "$LOCK_SECONDS" 8; then
    echo "deploy-service: $DEPLOY_LOCK was not free within $LOCK_SECONDS s" >&2
    exec 8>&-
    return 1
  fi
  "$@" || status=$?
  exec 8>&-
  return "$status"
}

ready() {
  local seconds=$1 deadline=$((SECONDS + $1)) reply
  while :; do
    curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$port/health/ready" 2>/dev/null && return 0
    ((SECONDS < deadline)) || break
    sleep "$READY_INTERVAL"
  done
  reply=$(curl -sS --max-time 3 -w ' (HTTP %{http_code})' "http://127.0.0.1:$port/health/ready" 2>&1) || true
  echo "deploy-service: $name is not ready after $seconds s: $reply" >&2
  return 1
}

# Every service's recorded ACL line, or its current release's before its first
# deploy here, rendered and loaded into both Redis instances.
assemble_acl() {
  local sources=() dir n
  for dir in "$SERVICES_ROOT"/*/; do
    dir=${dir%/}
    n=$(basename "$dir")
    if [[ -f $dir/acl/$n.acl ]]; then
      sources+=("$dir/acl/$n.acl")
    elif [[ -f $dir/current/redis/$n.acl ]]; then
      sources+=("$dir/current/redis/$n.acl")
    fi
  done
  # An empty ACL_SOURCES would fall back to assemble-acl.sh's own globs,
  # relative to the working directory. With no line to load, a glob that
  # matches nothing leaves the file to `ops` and the disabled `default`.
  ((${#sources[@]})) || sources=("$SERVICES_ROOT/*/acl/*.acl")
  REDIS_OPS_PASSWORD=$ops_password ACL_SOURCES="${sources[*]}" SVC_ENV_DIR=$SVC_ENV_DIR \
    bash "$tree/deploy/infra/redis/assemble-acl.sh"
}

stage() {
  local staging var value
  install -d -m 0755 "$home/releases" || return 1
  staging=$(mktemp -d "$home/releases/.staging.XXXXXX") || return 1
  cleanup_staging=$staging
  install -m 0644 "$src/compose.yaml" "$staging/compose.yaml" || return 1
  cp -R "$src/nginx" "$src/redis" "$staging/" || return 1
  if [[ -d $src/mongo ]]; then cp -R "$src/mongo" "$staging/" || return 1; fi
  # IMAGE_TAG, and every other variable the compose file interpolates without
  # a default, such as the signer's key id, from the service's own env file.
  (umask 077 && echo "IMAGE_TAG=$tag" >"$staging/release.env") || return 1
  for var in $(grep -oE '\$\{[A-Z_][A-Z0-9_]*\}' "$staging/compose.yaml" | tr -d '{}$' | sort -u); do
    [[ $var == IMAGE_TAG ]] && continue
    if ! value=$(env_value "$env_file" "$var"); then
      echo "deploy-service: compose.yaml needs $var, and $env_file has none" >&2
      return 1
    fi
    printf '%s=%s\n' "$var" "$value" >>"$staging/release.env" || return 1
  done
  rm -rf "$rel" && mv "$staging" "$rel" || return 1
  cleanup_staging=
}

load_acl() {
  install -d -m 0755 "$home/acl" &&
    install -m 0644 "$rel/redis/$name.acl" "$home/acl/$name.acl" &&
    assemble_acl
}

start_release() {
  local image
  "${compose[@]}" pull || return 1
  # `up` would build an image that is not on disk; the pull must have put it there.
  image=$("${compose[@]}" config --images) || return 1
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "deploy-service: $image is not on disk after the pull" >&2
    return 1
  fi
  container_changed=1
  "${compose[@]}" up -d --no-deps
}

install_include() {
  local dst=$NGINX_API_DIR/$name.conf
  install -d -m 0755 "$NGINX_API_DIR" &&
    install -m 0644 "$rel/nginx/$name.conf" "$dst" &&
    relabel "$dst" &&
    nginx -t &&
    systemctl reload nginx
}

switch_current() {
  ln -sfn "releases/$version" "$home/current.next" &&
    mv -fT "$home/current.next" "$home/current" || return 1
  current_changed=1
  # The unit starts the service at boot from `current`; on a running service
  # its `up -d` finds nothing to change.
  systemctl enable --quiet "mosaiq-svc@$name" || return 1
  systemctl is-active --quiet "mosaiq-svc@$name" || systemctl start "mosaiq-svc@$name"
}

restore_acl() {
  if [[ -s $work/acl.before ]]; then
    install -m 0644 "$work/acl.before" "$home/acl/$name.acl" || return 1
  else
    rm -f "$home/acl/$name.acl" || return 1
  fi
  assemble_acl
}

restore_include() {
  local dst=$NGINX_API_DIR/$name.conf
  if [[ -f $work/include.before ]]; then
    install -m 0644 "$work/include.before" "$dst" && relabel "$dst" || return 1
  else
    rm -f "$dst" || return 1
  fi
  nginx -t && systemctl reload nginx
}

rollback() {
  local step=$1 status=70
  trap '' HUP INT TERM
  exec 8>&-
  say "FAILED at step $step: restoring the previous release of $name"
  if ((current_changed)); then
    if [[ -n $previous_link ]]; then
      ln -sfn "$previous_link" "$home/current.next" &&
        mv -fT "$home/current.next" "$home/current" || status=71
    else
      rm -f "$home/current" || status=71
      systemctl disable --quiet "mosaiq-svc@$name" || status=71
    fi
  fi
  if ((container_changed)); then
    if [[ -n $previous ]]; then
      docker compose -p mosaiq-"$name" --env-file "$previous/release.env" \
        -f "$previous/compose.yaml" up -d --no-deps || status=71
    else
      "${compose[@]}" down || status=71
    fi
  fi
  if ((acl_changed)); then with_lock restore_acl || status=71; fi
  if ((include_changed)); then with_lock restore_include || status=71; fi
  if [[ -n $previous ]]; then
    if ready "$READY_SECONDS"; then
      echo "rolled back: $previous_link (image tag $(release_tag "$previous")) is serving again"
    else
      status=71
    fi
  elif ((status == 70)); then
    echo "rolled back: $name had no release here before, and none is running now"
  fi
  if ((status != 70)); then
    echo "ROLLBACK ALSO FAILED: $name needs a person (deploy/README.md, F18-03)" >&2
  fi
  exit "$status"
}

fail() {
  echo "deploy-service: step $1 failed: $2" >&2
  rollback "$1"
}

# 1. Preconditions, and the release serving now -------------------------------
say "1. $name $version: preconditions"
[[ ${REQUIRE_ROOT:-1} == 0 || $(id -u) -eq 0 ]] || die "run as root"
[[ -f $env_file ]] || die "refusing to start: $env_file is missing"
for file in compose.yaml VERSION "nginx/$name.conf" "redis/$name.acl"; do
  [[ -f $src/$file ]] || die "$src/$file is missing"
done
[[ $(<"$src/VERSION") == "$tag" ]] || die "services/$name/VERSION is $(<"$src/VERSION"), not $tag"
port=$(awk -v service="$name" '
  $1 == "-" && $2 == "name:" { selected = ($3 == service) }
  selected && $1 == "port:" { print $2; exit }
' "$tree/contracts/registry.yaml")
[[ $port =~ ^[0-9]+$ ]] || die "contracts/registry.yaml gives $name no port"
[[ -r $REDIS_OPS_ENV ]] || die "cannot read $REDIS_OPS_ENV"
ops_password=$(env_value "$REDIS_OPS_ENV" REDIS_OPS_PASSWORD) || die "$REDIS_OPS_ENV has no REDIS_OPS_PASSWORD"
systemctl cat "mosaiq-svc@$name.service" >/dev/null 2>&1 \
  || die "mosaiq-svc@.service is not installed (deploy/README.md, F18-01 step 8)"

previous=
previous_link=
if [[ -L $home/current ]]; then
  previous_link=$(readlink "$home/current")
  previous=$(readlink -f "$home/current")
  echo "serving: $previous_link (image tag $(release_tag "$previous"))"
else
  echo "serving: nothing; this is the first release of $name here"
fi
if [[ -n $previous && $previous == "$(readlink -m "$rel")" ]]; then
  say "Already serving $version: checking readiness"
  ready "$READY_SECONDS" || exit 1
  echo "ready"
  exit 0
fi

work=$(mktemp -d)
cleanup_staging=
trap 'rm -rf "$work" ${cleanup_staging:+"$cleanup_staging"}' EXIT
acl_changed=0 container_changed=0 include_changed=0 current_changed=0

# 2. Stage the release ----------------------------------------------------------
say "2. Staging $rel"
stage || die "staging $rel failed"

# From here on, a failure puts this service's previous release back.
trap 'fail signal "interrupted"' HUP INT TERM

# 3. ACL line, under the host lock ---------------------------------------------
say "3. Loading the ACL line of $name into redis-state and redis-cache"
if [[ -f $home/acl/$name.acl ]]; then
  cp "$home/acl/$name.acl" "$work/acl.before"
elif [[ -n $previous && -f $previous/redis/$name.acl ]]; then
  cp "$previous/redis/$name.acl" "$work/acl.before"
fi
acl_changed=1
with_lock load_acl || fail 3 "the ACL line was not loaded"

# 4. Pull and start --------------------------------------------------------------
say "4. Pulling image tag $tag and starting it"
start_release || fail 4 "the image was not pulled or did not start"

# 5. Readiness ------------------------------------------------------------------
say "5. Waiting up to $READY_SECONDS s for http://127.0.0.1:$port/health/ready"
ready "$READY_SECONDS" || fail 5 "the release never became ready"
echo "ready"

# 6. NGINX include, under the host lock -----------------------------------------
say "6. Installing $NGINX_API_DIR/$name.conf"
if [[ -f $NGINX_API_DIR/$name.conf ]]; then cp -p "$NGINX_API_DIR/$name.conf" "$work/include.before"; fi
include_changed=1
with_lock install_include || fail 6 "the include was refused or NGINX did not reload"

# 7. Switch current --------------------------------------------------------------
say "7. Pointing current at releases/$version"
switch_current || fail 7 "current was not switched"

trap - HUP INT TERM
say "Deployed $name $version"
echo "serving: releases/$version (image tag $tag); previous: ${previous_link:-none}"
