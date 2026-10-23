#!/usr/bin/env bash
# Start one image the way the conformance suite needs it, or take it down
# (F13-09). Run from the repository root.
#
#   tests/conformance/stack.sh up <image> <service-dir> <port>
#   tests/conformance/stack.sh logs
#   tests/conformance/stack.sh down
#
# `up` starts, on the external network `mosaiq`:
# - redis-state and redis-cache from deploy/infra/compose.yaml, as project
#   mosaiq-conformance, loading <service-dir>/redis/<name>.acl with a
#   throwaway password;
# - when <service-dir>/mongo/init.js exists, mongodb from the same file, with
#   --auth, and that init.js run in mongosh as root with MONGO_<SUFFIX>_PASSWORD
#   set; the image gets MONGO_URI for user svc_<suffix> in database <suffix>;
# - a PostgreSQL 16 built by the three scripts, whose socket the image mounts;
# - the image, hardened as its compose project runs it, listening on <port> and
#   published on 127.0.0.1:<port>, with a throwaway key's JWKS mounted at
#   /run/mosaiq/jwks.json. A service whose compose.yaml mounts a private key
#   (auth, the only one that signs) also gets that same throwaway key at
#   /run/mosaiq/auth-<kid>.pem, with AUTH_KID and AUTH_PRIVATE_KEY_PATH, so the
#   tokens the suite signs are the ones the service signs and verifies.
# It waits for /health/ready, then writes the suite's variables to
# $CONFORMANCE_ENV (default <work>/conformance.env).
#
# <name> is the basename of <service-dir>; <suffix> comes from the
# `user svc_<suffix>` line of its ACL file; <SUFFIX> is <suffix> upper-cased.
# Every step is keyed on a file, never on a service name.
set -euo pipefail

work=${CONFORMANCE_WORK:-${RUNNER_TEMP:-/tmp}/mosaiq-conformance}
project=mosaiq-conformance
label=mosaiq.conformance=1
infra=(docker compose -p "$project" -f deploy/infra/compose.yaml)

die() { echo "conformance: $*" >&2; exit 1; }
rand() { openssl rand -hex 24; }

infra_env() {
  export MOSAIQ_REDIS_ACL_DIR=$work/acl MOSAIQ_MONGO_ROOT_ENV=$work/mongodb-root.env
}

case ${1:-} in
  down)
    infra_env
    docker ps -aq --filter "label=$label" | xargs -r docker rm -f >/dev/null
    [[ -f $MOSAIQ_MONGO_ROOT_ENV ]] && "${infra[@]}" down --volumes >/dev/null 2>&1
    # PostgreSQL's entrypoint made the socket directory sticky and its own, so
    # what it left there stays until the runner is discarded.
    rm -rf "$work" 2>/dev/null || true
    exit 0
    ;;
  logs)
    infra_env
    for container in $(docker ps -aq --filter "label=$label"); do
      echo "== $(docker inspect -f '{{.Name}}' "$container")"
      docker logs --tail 200 "$container" 2>&1
    done
    "${infra[@]}" logs --no-color --tail 100 2>&1 || true
    exit 0
    ;;
  up) [[ $# -eq 4 ]] || die "usage: $0 up <image> <service-dir> <port>" ;;
  *) die "usage: $0 up <image> <service-dir> <port> | logs | down" ;;
esac

image=$2
service_dir=${3%/}
port=$4
name=$(basename "$service_dir")
acl=$service_dir/redis/$name.acl
[[ -f $acl ]] || die "$acl does not exist"
suffix=$(sed -n 's/^user svc_\([a-z][a-z0-9_]*\) .*/\1/p' "$acl")
[[ -n $suffix ]] || die "$acl has no 'user svc_<suffix>' line"
SUFFIX=${suffix^^}

[[ ! -e $work ]] || die "$work already exists; run '$0 down' first, or set CONFORMANCE_WORK"
mkdir -p "$work/env" "$work/acl" "$work/keys" "$work/pgsock"
infra_env
docker network inspect mosaiq >/dev/null 2>&1 || docker network create mosaiq >/dev/null

# Throwaway credentials, masked in a GitHub Actions log.
ops_password=$(rand)
redis_password=$(rand)
mongo_root_password=$(rand)
mongo_password=$(rand)
if [[ -n ${GITHUB_ACTIONS:-} ]]; then
  for secret in "$ops_password" "$redis_password" "$mongo_root_password" "$mongo_password"; do
    echo "::add-mask::$secret"
  done
fi

# Keyed on the file, not the name: only a compose.yaml that mounts a private
# key (ADR-0036) makes the service a signer.
signer=false
if [[ -f $service_dir/compose.yaml ]] && grep -q 'auth-\${AUTH_KID}\.pem' "$service_dir/compose.yaml"; then
  signer=true
fi

env_file=$work/env/$name.env
{
  echo "DATABASE_URL=postgresql://svc_$suffix@/retail?host=/run/postgresql"
  echo "REDIS_STATE_URL=redis://redis-state:6379/0"
  echo "REDIS_CACHE_URL=redis://redis-cache:6379/0"
  echo "REDIS_${SUFFIX}_PASSWORD=$redis_password"
  echo "AUTH_ISSUER=https://auth.invalid/"
  echo "AUTH_JWKS_PATH=/run/mosaiq/jwks.json"
  echo "MOSAIQ_SERVICE_NAME=$name"
  # The suite sends a few hundred requests from one subject a minute.
  echo "RATE_LIMIT_REQUESTS=100000"
} >"$env_file"
printf 'MONGO_INITDB_ROOT_USERNAME=root\nMONGO_INITDB_ROOT_PASSWORD=%s\n' \
  "$mongo_root_password" >"$MOSAIQ_MONGO_ROOT_ENV"

# The aclfile both instances load, assembled by the instance's own script.
ACL_SOURCES=$acl SVC_ENV_DIR=$work/env ACL_FILE=$work/acl/users.acl \
  REDIS_OPS_PASSWORD=$ops_password bash deploy/infra/redis/assemble-acl.sh --write-only
"${infra[@]}" up -d --quiet-pull redis-state redis-cache
redis_state=$("${infra[@]}" ps -q redis-state)
redis_cache=$("${infra[@]}" ps -q redis-cache)

# The key pair: only its public half reaches the image.
kid=conformance-$(openssl rand -hex 4)
openssl genrsa -out "$work/keys/private.pem" 2048 2>/dev/null
python3 tests/conformance/tokens.py jwks "$work/keys/private.pem" "$kid" >"$work/keys/jwks.json"
chmod 0444 "$work/keys/jwks.json"
key_mounts=()
if [[ $signer == true ]]; then
  {
    echo "AUTH_KID=$kid"
    echo "AUTH_PRIVATE_KEY_PATH=/run/mosaiq/auth-$kid.pem"
    echo "AUTH_ACCESS_TOKEN_TTL_SECONDS=600"
    echo "AUTH_SESSION_ABSOLUTE_TTL_SECONDS=43200"
  } >>"$env_file"
  # Throwaway and per run; the container runs as UID 10001, not the runner.
  chmod 0444 "$work/keys/private.pem"
  key_mounts=(-v "$work/keys/private.pem:/run/mosaiq/auth-$kid.pem:ro")
fi

docker run -d --name "$project-postgres" --label "$label" \
  -e POSTGRES_PASSWORD=postgres -e APP_DB_PASSWORD=retail_app \
  -v "$work/pgsock:/var/run/postgresql" \
  -v "$PWD/sql:/opt/sql:ro" \
  -v "$PWD/docker/initdb.sh:/docker-entrypoint-initdb.d/initdb.sh:ro" \
  postgres:16 >/dev/null

if [[ -f $service_dir/mongo/init.js ]]; then
  "${infra[@]}" up -d --quiet-pull mongodb
  mongodb=$("${infra[@]}" ps -q mongodb)
  auth='db.getSiblingDB("admin").auth(process.env.MONGO_INITDB_ROOT_USERNAME, process.env.MONGO_INITDB_ROOT_PASSWORD)'
  # mongo_eval <script> [docker exec option...]; root's credentials are in the
  # container's environment, from its env_file.
  mongo_eval() {
    local script=$1
    shift
    docker exec "$@" "$mongodb" mongosh --quiet --norc mongodb://127.0.0.1:27017/admin --eval "$script"
  }
  for _ in $(seq 1 60); do
    mongo_eval "$auth" >/dev/null 2>&1 && break
    sleep 2
  done
  # docker exec copies the value from this environment, so it is in no argv.
  export "MONGO_${SUFFIX}_PASSWORD=$mongo_password"
  mongo_eval "$auth; $(cat "$service_dir/mongo/init.js")" -e "MONGO_${SUFFIX}_PASSWORD" \
    || die "$service_dir/mongo/init.js failed"
  echo "MONGO_URI=mongodb://svc_$suffix:$mongo_password@mongodb:27017/$suffix?authSource=$suffix" >>"$env_file"
fi

# The init scripts run against a temporary server, then the real one starts.
for _ in $(seq 1 120); do
  log=$(docker logs "$project-postgres" 2>&1)
  if [[ $log == *"PostgreSQL init process complete"* ]] \
    && docker exec "$project-postgres" pg_isready -q -U postgres -d retail; then
    break
  fi
  sleep 1
done
docker exec "$project-postgres" pg_isready -q -U postgres -d retail \
  || { docker logs "$project-postgres" 2>&1 | tail -40; die "PostgreSQL is not ready"; }
# A real service's role comes from the scripts; a kit example's does not.
docker exec -i "$project-postgres" psql -X -q -v ON_ERROR_STOP=1 -U postgres -d retail \
  -v role="svc_$suffix" >/dev/null <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SQL

docker run -d --name "$project-$name" --label "$label" --network mosaiq \
  --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges:true \
  --read-only --tmpfs /tmp:uid=10001,gid=10001,mode=0700 \
  --env-file "$env_file" \
  -v "$work/pgsock:/run/postgresql:ro" \
  -v "$work/keys/jwks.json:/run/mosaiq/jwks.json:ro" \
  "${key_mounts[@]}" \
  -p "127.0.0.1:$port:$port" \
  "$image" >/dev/null

status=000
for _ in $(seq 1 90); do
  status=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/health/ready" || true)
  case $status in 2??) break ;; esac
  sleep 1
done
case $status in
  2??) ;;
  *) docker logs "$project-$name" 2>&1 | tail -60; die "/health/ready answered $status" ;;
esac

{
  echo "CONFORMANCE_BASE_URL=http://127.0.0.1:$port"
  echo "CONFORMANCE_SERVICE=$name"
  echo "CONFORMANCE_PRIVATE_KEY=$work/keys/private.pem"
  echo "CONFORMANCE_KID=$kid"
  echo "CONFORMANCE_REDIS_STATE=$redis_state"
  echo "CONFORMANCE_REDIS_CACHE=$redis_cache"
  echo "REDIS_OPS_PASSWORD=$ops_password"
} >"${CONFORMANCE_ENV:-$work/conformance.env}"
echo "conformance: $image is ready on 127.0.0.1:$port"
