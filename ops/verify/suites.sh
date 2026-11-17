#!/usr/bin/env bash
# Every suite against one commit, inside the toolbox (F20-03, #761). Run by
# entrypoint.sh from the checked-out commit; ops/verify/README.md has the
# order and the report.
#
# Each step mirrors the workflow step that runs it in CI (named beside it) and
# leaves one JUnit file under /report/results/<suite>/. A step that fails does
# not stop the run: every suite runs, and the report decides the exit status.
set -uo pipefail

R=/report
REPORT="python3 /opt/verify/report.py"
COMPOSE=(docker compose -f compose.distributed.yaml)
# Image builds use the toolbox's own network namespace: under Rosetta, the
# inner daemon cannot run a build step in a bridge sandbox (its prestart hook
# re-executes dockerd). The namespace is the container's, not the host's.
BUILD_NET=$R/tmp/build-network.yaml
BUILD=(docker compose -f compose.distributed.yaml -f "$BUILD_NET")
export PGHOST=localhost PGUSER=postgres PGPASSWORD=postgres
export CARGO_TERM_COLOR=never
mkdir -p "$R/logs" "$R/results" "$R/tmp"
started=$(date -u +%FT%TZ)

log_of() { mkdir -p "$R/logs/$1" && echo "$R/logs/$1/$2.log"; }

# check <suite> <step> <command...>: one case, passed when the command exits 0.
check() {
  local suite=$1 step=$2 log status
  shift 2
  log=$(log_of "$suite" "$step")
  echo "== $suite / $step"
  "$@" >"$log" 2>&1
  status=$?
  $REPORT check "$R" "$suite" "$step" "$status" "$log"
  return "$status"
}

# quiet: the command passes only when it exits 0 and prints nothing (ci.yml's
# run_quiet, for checks that print one line per problem).
quiet() {
  local out status
  out=$("$@" 2>&1)
  status=$?
  printf '%s\n' "$out"
  ((status == 0)) && [[ -z $out ]]
}

# tests <suite> <step> <junit-glob> <command...>: adopt the runner's JUnit.
tests() {
  local suite=$1 step=$2 glob=$3 log status
  shift 3
  log=$(log_of "$suite" "$step")
  echo "== $suite / $step"
  "$@" >"$log" 2>&1
  status=$?
  # shellcheck disable=SC2086
  $REPORT junit "$R" "$suite" "$step" "$status" "$log" $glob
  return "$status"
}

# cargo_tests <suite> <step> <command...>: cargo test's output as JUnit.
cargo_tests() {
  local suite=$1 step=$2 log status
  shift 2
  log=$(log_of "$suite" "$step")
  echo "== $suite / $step"
  "$@" >"$log" 2>&1
  status=$?
  $REPORT libtest "$R" "$suite" "$step" "$status" "$log"
  return "$status"
}

# not_run <suite> <step> <reason>: a step that could not start is a failure.
not_run() {
  local log
  log=$(log_of "$1" "$2")
  echo "could not run: $3" >"$log"
  $REPORT check "$R" "$1" "$2" 1 "$log"
}

# ---- Throwaway infrastructure for the unit suites (services.yml, ci.yml) ----

redis_image() { sed -nE 's/^ *image: (redis:[^ ]+)$/\1/p' deploy/infra/compose.yaml | head -1; }
mongo_image() { sed -nE 's/^ *image: (mongo:[^ ]+)$/\1/p' deploy/infra/compose.yaml | head -1; }

# A PostgreSQL 18 on 127.0.0.1:5432, empty, as each CI job's service container.
pg_fresh() {
  docker rm -f verify-pg >/dev/null 2>&1
  docker run -d --name verify-pg -e POSTGRES_PASSWORD=postgres \
    -p 127.0.0.1:5432:5432 postgres:18 >/dev/null || return 1
  for _ in $(seq 1 60); do
    pg_isready -q && psql -tAc 'SELECT 1' >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

pg_load() {
  psql -v ON_ERROR_STOP=1 -f sql/00_create_database.sql &&
    psql -v ON_ERROR_STOP=1 -d retail -f sql/01_schema.sql &&
    psql -v ON_ERROR_STOP=1 -d retail -f sql/02_seed_30_per_table.sql
}

# Redis on 127.0.0.1:6379 with appendonly on; with a password when given.
redis_fresh() {
  docker rm -f verify-redis >/dev/null 2>&1
  local args=(redis-server --appendonly yes --appendfsync everysec)
  [[ -n ${1:-} ]] && args+=(--requirepass "$1")
  docker run -d --name verify-redis -p 127.0.0.1:6379:6379 "$(redis_image)" "${args[@]}" >/dev/null || return 1
  for _ in $(seq 1 30); do
    docker exec -e REDISCLI_AUTH="${1:-}" verify-redis redis-cli PING 2>/dev/null | grep -q PONG && return 0
    sleep 1
  done
  return 1
}

infra_down() { docker rm -f verify-pg verify-redis verify-mongo >/dev/null 2>&1 || true; }

# ---- Tools ----

setup_tools() {
  pip install -q -r web/requirements-dev.txt -r contracts/requirements.txt &&
    bash contracts/install-tools.sh
}

record_tools() {
  {
    echo "python=$(python3 --version 2>&1)"
    echo "pip-pytest=$(python3 -m pytest --version 2>&1 | head -1)"
    echo "black=$(black --version 2>&1 | head -1)"
    echo "ruff=$(ruff --version 2>&1)"
    echo "uv=$(uv --version 2>&1)"
    echo "node=$(node --version 2>&1)"
    echo "npm=$(npm --version 2>&1)"
    echo "mongosh=$(mongosh --version 2>&1)"
    echo "java=$(java -version 2>&1 | head -1)"
    echo "gradle=$(sed -n 's/^distributionUrl=.*gradle-\(.*\)-bin.zip$/\1/p' clients/android/gradle/wrapper/gradle-wrapper.properties)"
    echo "rustup=$(rustup --version 2>/dev/null | head -1)"
    for toolchain in $(rustup toolchain list 2>/dev/null | cut -d' ' -f1); do
      echo "rustc-${toolchain%%-*}=$(rustc +"$toolchain" --version 2>&1)"
    done
    echo "docker=$(docker version --format '{{.Server.Version}}' 2>&1)"
    echo "compose=$(docker compose version --short 2>&1)"
    echo "psql=$(psql --version 2>&1)"
    echo "initdb=$(/usr/lib/postgresql/16/bin/initdb --version 2>&1)"
    echo "jq=$(jq --version 2>&1)"
    echo "yq=$(yq --version 2>&1)"
    echo "oasdiff=$(oasdiff --version 2>&1)"
    echo "openapi-spec-validator=$(openapi-spec-validator --version 2>&1 | head -1)"
    echo "xmllint=$(xmllint --version 2>&1 | head -1)"
    echo "kernel=$(uname -srm)"
  } >"$R/tools.txt"
}

record_images() {
  docker image ls --no-trunc --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Digest}}' \
    | grep -v '<none>:<none>' | sort >"$R/images.txt"
}

write_meta() {
  python3 - "$R" "$started" "$(date -u +%FT%TZ)" <<'PY'
import json, os, subprocess, sys
report, started, finished = sys.argv[1:]
def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()
tools = {}
for line in open(f"{report}/tools.txt", encoding="utf-8"):
    key, _, value = line.rstrip("\n").partition("=")
    tools[key] = value
images = {}
for line in open(f"{report}/images.txt", encoding="utf-8"):
    name, image_id, digest = (line.split() + ["", ""])[:3]
    # A pulled image is named by its registry digest, a built one by its ID.
    images[name] = digest if digest.startswith("sha256:") else image_id
json.dump({
    "commit": git("rev-parse", "HEAD"),
    "subject": git("log", "-1", "--format=%s"),
    "started": started,
    "finished": finished,
    "runner_commit": os.environ.get("VERIFY_RUNNER_COMMIT", ""),
    "toolbox": os.environ.get("VERIFY_TOOLBOX", ""),
    "images": images,
    "tools": tools,
}, open(f"{report}/meta.json", "w"), indent=2, sort_keys=True)
PY
}

# ---- Suites ----

suite_toolbox() {
  check toolbox install setup_tools
}

# Every image at this commit, as nightly.yml and the conformance suite use them.
suite_images() {
  docker network inspect mosaiq >/dev/null 2>&1 || docker network create mosaiq >/dev/null
  check images stage-contracts bash .github/scripts/stage-contracts.sh --all
  check images auth-keys bash docker/distributed/auth-keys.sh
  check images pull "${COMPOSE[@]}" pull --ignore-buildable --quiet
  {
    echo "services:"
    "${COMPOSE[@]}" config --format json | jq -r '.services | to_entries[] | select(.value.build) | .key' \
      | while read -r service; do printf '  %s:\n    build:\n      network: host\n' "$service"; done
  } >"$BUILD_NET"
  # VERIFY_BUILD_SERIAL=1 builds one image at a time, for a small machine.
  if [[ ${VERIFY_BUILD_SERIAL:-0} == 1 ]]; then
    local service
    for service in $(sed -n 's/^  \([a-z][a-z0-9-]*\):$/\1/p' "$BUILD_NET"); do
      check images "build-$service" "${BUILD[@]}" build "$service"
    done
  else
    check images build "${BUILD[@]}" build
  fi
  check images postgres-18 docker pull -q postgres:18
  check images postgres-16 docker pull -q postgres:16
  # The images compose.distributed.yaml does not reach, each as its workflow
  # builds it: the web (ci.yml), the ops console (ops-console.yml) and the
  # bench twin (bench.yml).
  check images build-web docker build --network=host -t mosaiq-web:verify .
  check images build-ops-console bash -c 'cp contracts/registry.yaml ops/console/registry.yaml &&
    docker build --network=host -t mosaiq-ops-console:verify ops/console'
  check images build-bench bash -c 'd=bench/products-service-rs && rm -rf "$d/contracts" &&
    mkdir -p "$d/contracts/common" && cp -R contracts/products "$d/contracts/products" &&
    cp -R contracts/common/v1 "$d/contracts/common/v1" &&
    docker build --network=host -t products-service-rs:verify "$d"'
}

# ci.yml `contracts` and `guards`.
suite_contracts() {
  check contracts check quiet bash contracts/check.sh
  check contracts streams quiet bash contracts/streams/check_streams.sh
  check contracts guards quiet bash .github/scripts/guards.sh
  check contracts memory-gate python3 .github/scripts/memory_gate.py
}

# ci.yml `quality`: format, lint and the repository pytest. The five suites
# that get their own section of the report are left to it.
suite_web() {
  check web black black --check .
  check web ruff ruff check .
  tests web pytest test-results/pytest.xml \
    python3 -m pytest -rs -p no:cacheprovider --junitxml=test-results/pytest.xml \
    --ignore=tests/golden --ignore=tests/e2e --ignore=tests/negative --ignore=tests/conformance \
    --ignore=tests/parity
}

# ci.yml `database`: every `run` step of that job, read from the workflow
# itself so the two never drift, against an empty PostgreSQL 18. It holds the
# SQL checks and the web's database-backed tests.
suite_database() {
  pg_fresh || { not_run database postgres "PostgreSQL 18 did not start"; return; }
  mkdir -p test-results && chmod 1777 test-results
  local count i name script envs step
  count=$(yq -r '.jobs.database.steps | length' .github/workflows/ci.yml)
  for ((i = 0; i < count; i++)); do
    script=$(yq -r ".jobs.database.steps[$i].run // empty" .github/workflows/ci.yml)
    [[ -n $script && $script != pip\ install* ]] || continue
    name=$(yq -r ".jobs.database.steps[$i].name // \"step-$i\"" .github/workflows/ci.yml)
    step=$(tr 'A-Z ' 'a-z-' <<<"$name" | tr -cd 'a-z0-9-')
    envs=()
    while IFS= read -r line; do
      [[ -n $line ]] && envs+=("$line")
    done < <(yq -r ".jobs.database.steps[$i].env // {} | to_entries[] | \"\(.key)=\(.value)\"" .github/workflows/ci.yml)
    rm -rf test-results/pytest-*.xml
    # As the runner's unprivileged user: the scratch-cluster tests call
    # initdb, which refuses root. The environment is kept.
    if grep -q -- '--junitxml' <<<"$script"; then
      tests database "$step" 'test-results/pytest-*.xml' runuser -u runner -- \
        env "${envs[@]}" bash -eo pipefail -c "$script"
    else
      check database "$step" runuser -u runner -- env "${envs[@]}" bash -eo pipefail -c "$script"
    fi
  done
  infra_down
}

# tests/golden's own tests and golden-drift.yml's capture and compare.
suite_golden() {
  pg_fresh && pg_load >"$(log_of golden load)" 2>&1 || { not_run golden load "the database did not load"; return; }
  export DATABASE_URL=postgresql://retail_app:retail_app@localhost:5432/retail
  tests golden pytest test-results/golden.xml \
    python3 -m pytest tests/golden -rs -p no:cacheprovider --junitxml=test-results/golden.xml
  local out=/tmp/golden
  rm -rf "$out"
  check golden capture bash -c "for s in sales recommendations users campaigns; do
      python3 -m tests.golden.capture --service \$s --out $out || exit 1; done"
  check golden drift python3 -m tests.golden.compare "$out"
  unset DATABASE_URL
  infra_down
}

# services.yml `service`, one service at a time: its own fresh database,
# Redis and MongoDB, its own lock file and its own suite.
service_unit() {
  local name=$1 dir=services/$1 suite=service-$1 kind suffix
  check "$suite" version grep -qxE '(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)' "$dir/VERSION"
  if [[ -f $dir/uv.lock && ! -f $dir/Cargo.lock ]]; then kind=python
  elif [[ -f $dir/Cargo.lock && ! -f $dir/uv.lock ]]; then kind=rust
  else not_run "$suite" kind "needs exactly one of uv.lock and Cargo.lock"; return; fi

  pg_fresh && pg_load >"$(log_of "$suite" load)" 2>&1 \
    || { not_run "$suite" load "the database did not load"; return; }
  local env=(TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/retail MOSAIQ_REQUIRE_POSTGRES=1)
  if compgen -G "$dir/redis/*.acl" >/dev/null; then
    redis_fresh || { not_run "$suite" redis "Redis did not start"; return; }
    env+=(TEST_REDIS_URL=redis://127.0.0.1:6379/0 MOSAIQ_REQUIRE_REDIS=1)
  fi
  if [[ -f $dir/mongo/init.js ]]; then
    suffix=$(sed -nE 's/^user svc_([a-z0-9_]+) .*/\1/p' "$dir/redis/$name.acl")
    local root password root_uri
    root=$(openssl rand -hex 16) password=$(openssl rand -hex 12)
    docker run -d --name verify-mongo -p 127.0.0.1:27017:27017 \
      -e MONGO_INITDB_ROOT_USERNAME=root -e MONGO_INITDB_ROOT_PASSWORD="$root" \
      "$(mongo_image)" mongod --auth >/dev/null
    root_uri="mongodb://root:$root@127.0.0.1:27017/admin"
    for _ in $(seq 1 60); do
      mongosh --quiet --norc "$root_uri" --eval 'db.runCommand({ping: 1})' >/dev/null 2>&1 && break
      sleep 2
    done
    env+=("MONGO_${suffix^^}_PASSWORD=$password")
    check "$suite" mongo-init env "MONGO_${suffix^^}_PASSWORD=$password" \
      mongosh --quiet --norc "$root_uri" --file "$dir/mongo/init.js"
    env+=("TEST_MONGO_ROOT_URI=$root_uri"
      "TEST_MONGO_URI=mongodb://svc_$suffix:$password@127.0.0.1:27017/$suffix?authSource=$suffix"
      MOSAIQ_REQUIRE_MONGO=1)
  fi

  if [[ $kind == python ]]; then
    check "$suite" uv-sync bash -c "cd $dir && uv sync --frozen"
    check "$suite" black bash -c "cd $dir && uv run black --check ."
    check "$suite" ruff bash -c "cd $dir && uv run ruff check ."
    tests "$suite" pytest "$R/tmp/$suite.xml" env "${env[@]}" \
      bash -c "cd $dir && uv run pytest -rs -p no:cacheprovider --junitxml=$R/tmp/$suite.xml"
  else
    local channel
    if [[ -f $dir/rust-toolchain.toml ]]; then
      channel=$(sed -n 's/^channel = "\(.*\)"$/\1/p' "$dir/rust-toolchain.toml")
    else
      channel=$(sed -n 's/^FROM rust:\([0-9.]*\)-.*/\1/p' "$dir/Dockerfile" | head -1)
    fi
    check "$suite" toolchain rustup toolchain install "$channel" --profile minimal --component clippy,rustfmt
    check "$suite" fmt bash -c "cd $dir && cargo +$channel fmt --check"
    check "$suite" clippy bash -c "cd $dir && cargo +$channel clippy --locked --all-targets -- -D warnings"
    cargo_tests "$suite" cargo-test env "${env[@]}" bash -c "cd $dir && cargo +$channel test --locked"
    rm -rf "$dir/target"
  fi
  infra_down
}

# services.yml's conformance step, against the image the stack runs.
service_conformance() {
  local name=$1 port image=ghcr.io/equipo-03-proyecto10/mosaiq-$1:local
  port=$(yq -r --arg n "$name" '.services[] | select(.name == $n) | .port' contracts/registry.yaml)
  docker image inspect "$image" >/dev/null 2>&1 || { not_run "conformance-$name" run "$image was not built"; return; }
  rm -f "test-results/conformance-$name.xml"
  tests "conformance-$name" suite "test-results/conformance-$name.xml" \
    bash tests/conformance/run.sh "$image" "services/$name" "$port"
}

suite_services() {
  local dockerfile name
  for dockerfile in services/*/Dockerfile; do
    name=$(basename "$(dirname "$dockerfile")")
    service_unit "$name"
    service_conformance "$name"
  done
}

# ci.yml `kit-py` and `kit-rs`, then services.yml `kits`.
suite_kits() {
  local password
  password=$(openssl rand -hex 24)
  pg_fresh && pg_load >"$(log_of kit-py load)" 2>&1 && redis_fresh "$password" \
    || { not_run kit-py infra "PostgreSQL or Redis did not start"; not_run kit-rs infra "PostgreSQL or Redis did not start"; return; }
  local env=(KIT_REQUIRE_SERVICES=1 KIT_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/retail
    "KIT_TEST_REDIS_URL=redis://:$password@127.0.0.1:6379/0")
  (
    cd packages/kit-py || exit 1
    check kit-py uv-sync uv sync --frozen
    check kit-py black uv run black --check .
    check kit-py ruff uv run ruff check .
    tests kit-py pytest "$R/tmp/kit-py.xml" env "${env[@]}" uv run pytest -rs -p no:cacheprovider --junitxml="$R/tmp/kit-py.xml"
    check kit-py example bash -c 'uv build --wheel && mkdir -p example/wheels example/contracts/common &&
      cp dist/*.whl example/wheels/ && cp -R ../../contracts/common/v1 example/contracts/common/ &&
      docker build --network=host -t mosaiq-kit-example example &&
      test "$(docker run --rm --entrypoint id mosaiq-kit-example -u)" = 10001'
  )
  (
    cd packages/kit-rs || exit 1
    local channel
    channel=$(sed -n 's/^channel = "\(.*\)"$/\1/p' rust-toolchain.toml)
    check kit-rs toolchain rustup toolchain install "$channel" --profile minimal --component clippy,rustfmt
    check kit-rs fmt cargo +"$channel" fmt --check
    check kit-rs clippy cargo +"$channel" clippy --locked --all-targets -- -D warnings
    cargo_tests kit-rs cargo-test env "${env[@]}" cargo +"$channel" test --locked
    check kit-rs example bash -c 'mkdir -p examples/contracts/common &&
      cp -R ../../contracts/common/v1 examples/contracts/common/ &&
      docker build --network=host -f examples/Dockerfile -t mosaiq-kit-rs-example . &&
      test "$(docker run --rm --entrypoint id mosaiq-kit-rs-example -u)" = 10001'
    rm -rf target
  )
  infra_down

  rm -f test-results/conformance-kit-example*.xml
  CONFORMANCE_CONTRACT_DIR=packages/kit-py/example/contracts/kit-example \
    tests kit-py conformance test-results/conformance-kit-example.xml \
    bash tests/conformance/run.sh mosaiq-kit-example tests/conformance/kit-example 8000
  check kit-py no-liveness-image docker build --network=host --build-arg BASE=mosaiq-kit-example \
    -t mosaiq-kit-example-no-liveness -f tests/conformance/kit-example/Dockerfile.no-liveness tests/conformance/kit-example
  CONFORMANCE_CONTRACT_DIR=packages/kit-py/example/contracts/kit-example \
    check kit-py conformance-not-vacuous bash tests/conformance/run.sh mosaiq-kit-example-no-liveness \
    tests/conformance/kit-example 8000 test_authentication_fails_closed_with_redis_state_stopped
  rm -f test-results/conformance-kit-example*.xml
  CONFORMANCE_CONTRACT_DIR=packages/kit-rs/examples/contracts/kit-example \
    tests kit-rs conformance test-results/conformance-kit-example.xml \
    bash tests/conformance/run.sh mosaiq-kit-rs-example tests/conformance/kit-example 8000
}

# ops-console.yml `check`: the console's own lock, format, lint and tests,
# and the host snapshot script. Its image is built with the others.
suite_ops_console() {
  (
    cd ops/console || exit 1
    check ops-console uv-sync uv sync --frozen
    check ops-console black uv run black --check .
    check ops-console ruff uv run ruff check .
    tests ops-console pytest "$R/tmp/ops-console.xml" \
      uv run pytest -rs -p no:cacheprovider --junitxml="$R/tmp/ops-console.xml"
  )
  check ops-console snapshot-parses bash -n ops/console/host/mosaiq-ops-snapshot.sh
}

# bench.yml `twin`: ADR-0042's checks, read from the workflow so the two never
# drift, the twin's Rust gates on a loaded PostgreSQL 18, and the shared
# conformance suite against the image built with the others.
suite_bench() {
  local dir=bench/products-service-rs channel guard staged=$R/tmp/products-bench
  guard=$(yq -r '.jobs.twin.steps[] | select(.name | test("^Never registered")) | .run' .github/workflows/bench.yml)
  check bench adr-0042 env TWIN=products-service-rs bash -eo pipefail -c "$guard"
  pg_fresh && pg_load >"$(log_of bench load)" 2>&1 || { not_run bench load "the database did not load"; return; }
  channel=$(sed -n 's/^FROM rust:\([0-9.]*\)-.*/\1/p' "$dir/Dockerfile" | head -1)
  check bench toolchain rustup toolchain install "$channel" --profile minimal --component clippy,rustfmt
  check bench fmt bash -c "cd $dir && cargo +$channel fmt --check"
  check bench clippy bash -c "cd $dir && cargo +$channel clippy --locked --all-targets -- -D warnings"
  cargo_tests bench cargo-test env TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/retail \
    MOSAIQ_REQUIRE_POSTGRES=1 bash -c "cd $dir && cargo +$channel test --locked"
  rm -rf "$dir/target"
  infra_down
  docker image inspect products-service-rs:verify >/dev/null 2>&1 \
    || { not_run bench conformance "products-service-rs:verify was not built"; return; }
  rm -rf "$staged" && mkdir -p "$staged/redis" && cp bench/redis/products-bench.acl "$staged/redis/"
  rm -f test-results/conformance-products-bench.xml
  CONFORMANCE_CONTRACT_DIR=contracts/products \
    tests bench conformance test-results/conformance-products-bench.xml \
    bash tests/conformance/run.sh products-service-rs:verify "$staged" 5103
}

# desktop.yml `check`, without the Playwright window tests and packaging.
suite_desktop() {
  (
    cd clients/desktop || exit 1
    check desktop npm-ci npm ci --no-audit --no-fund
    check desktop lint npm run lint
    check desktop format npm run format:check
    check desktop typecheck npm run typecheck
    check desktop bundle-contracts npm run bundle:contracts
    rm -f "$R/tmp/desktop.xml"
    tests desktop vitest "$R/tmp/desktop.xml" \
      npx vitest run --reporter=default --reporter=junit --outputFile.junit="$R/tmp/desktop.xml"
    check desktop adr41 quiet npm run --silent check:adr41
  )
}

# android.yml `check`: ktlint, Android lint and the unit tests.
suite_android() {
  (
    cd clients/android || exit 1
    check android ktlint ./gradlew --no-daemon ktlintCheck
    check android lint ./gradlew --no-daemon lint
    tests android unit-tests 'app/build/test-results/testDebugUnitTest/*.xml' \
      ./gradlew --no-daemon testDebugUnitTest
  )
  check android adr41 quiet bash clients/android/check-adr41.sh
}

# nightly.yml: the stack from empty, every boundary section re-applied.
suite_stack() {
  check stack up "${COMPOSE[@]}" up --wait --wait-timeout 900 || return 1
  check stack boundaries bash .github/scripts/apply-boundaries.sh \
    "${COMPOSE[@]}" exec -T db psql -U postgres -d retail
}

# tests/negative/README.md, "The local stack". It runs after tests/e2e: its
# many sign-ins leave auth's sign-in rate limit exhausted, which would refuse
# every sign-in e2e makes.
suite_negative() {
  local db
  db=$("${COMPOSE[@]}" ps -q db)
  docker exec "$db" mkdir -p /repo/contracts && docker cp contracts/db "$db:/repo/contracts/" >/dev/null
  rm -f test-results/negative.xml
  tests negative suite test-results/negative.xml env \
    MOSAIQ_BASE_URL=http://127.0.0.1:8080 \
    MOSAIQ_SIGNING_KEY=docker/distributed/keys/auth-local-dev.pem \
    MOSAIQ_DIRECT_PORTS=1 \
    MOSAIQ_EVIDENCE_FILE="$R/negative-evidence.md" \
    "MOSAIQ_DB_EXEC=docker exec -i -w /repo -e PGUSER=postgres -e PGDATABASE=retail $db" \
    MOSAIQ_REDIS_STATE_CONTAINER="$("${COMPOSE[@]}" ps -q redis-state)" \
    MOSAIQ_DB_CONTAINER="$db" \
    MOSAIQ_CUSTOMERS_CONTAINER="$("${COMPOSE[@]}" ps -q customers)" \
    python3 -m pytest tests/negative -rs -p no:cacheprovider --junitxml=test-results/negative.xml
}

# tests/e2e stops redis-state; every service is restarted and redis-state
# brought back before tests/negative.
restart_services() {
  local services
  services=$(ls services/*/compose.yaml | cut -d/ -f2 | tr '\n' ' ')
  # shellcheck disable=SC2086
  check stack restart-services bash -c "${COMPOSE[*]} restart $services && ${COMPOSE[*]} up --wait --wait-timeout 600"
}

# nightly.yml's parity step (F20-07, #765): tests/parity against the stack.
# The web system is not part of the stack (Q-12 keeps it off network
# `mosaiq`), so gunicorn runs in the toolbox against the db container's bridge
# IP, as on the nightly runner. The accounts are the seed's demonstration
# accounts. The harness's differences go to parity.json beside the report.
# It runs before tests/negative, whose sign-ins exhaust auth's rate limit.
suite_parity() {
  local db_ip pid up=1
  db_ip=$(docker inspect "$("${COMPOSE[@]}" ps -q db)" \
    --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
  mkdir -p "$R/tmp/web-uploads"
  DATABASE_URL="postgresql://retail_app:retail_app@$db_ip:5432/retail" \
    FLASK_ENV=development FLASK_SECRET_KEY="$(openssl rand -hex 32)" \
    UPLOAD_DIR="$R/tmp/web-uploads" \
    gunicorn --bind 127.0.0.1:5000 'web.app:create_app()' >"$(log_of parity web)" 2>&1 &
  pid=$!
  for _ in $(seq 1 60); do
    curl -fsS -o /dev/null http://127.0.0.1:5000/ && { up=0; break; }
    sleep 1
  done
  if ((up)); then
    kill "$pid" 2>/dev/null
    not_run parity suite "the web system did not answer on 127.0.0.1:5000"
    return
  fi
  rm -f test-results/parity.xml
  tests parity suite test-results/parity.xml env \
    PARITY_WEB_URL=http://127.0.0.1:5000 PARITY_GATEWAY_URL=http://127.0.0.1:8080 \
    "PARITY_DATABASE_URL=postgresql://postgres:postgres@$db_ip:5432/retail" \
    PARITY_WEB_EMAIL=admin@mosaiq-demo.com PARITY_WEB_PASSWORD='Password123!' \
    PARITY_SERVICE_EMAIL=user3@mosaiq-demo.com PARITY_SERVICE_PASSWORD='Password123!' \
    "PARITY_RESULTS=$R/parity.json" \
    python3 -m pytest tests/parity -rs -p no:cacheprovider --junitxml=test-results/parity.xml
  kill "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  return 0
}

suite_e2e() {
  rm -f test-results/e2e.xml
  tests e2e suite test-results/e2e.xml env E2E_BASE_URL=http://127.0.0.1:8080 E2E_COMPOSE=compose.distributed.yaml \
    python3 -m pytest tests/e2e -rs -p no:cacheprovider --junitxml=test-results/e2e.xml
}

collect_stack_logs() {
  mkdir -p "$R/logs/stack"
  "${COMPOSE[@]}" ps -a >"$R/logs/stack/ps.txt" 2>&1 || true
  "${COMPOSE[@]}" logs --no-color --timestamps >"$R/logs/stack/compose.log" 2>&1 || true
}

# ---- The run ----
# The unit suites and the conformance suite start their own throwaway
# containers and the conformance stack joins network `mosaiq` under the same
# names as the local stack, so the local stack comes up only after them.

suite_toolbox
suite_images
suite_contracts
suite_web
suite_database
suite_golden
suite_services
suite_kits
suite_bench
suite_ops_console
suite_desktop
suite_android
if suite_stack; then
  suite_e2e
  restart_services
  suite_parity
  suite_negative
else
  not_run e2e suite "the local stack did not come up"
  not_run parity suite "the local stack did not come up"
  not_run negative suite "the local stack did not come up"
fi
collect_stack_logs
"${COMPOSE[@]}" down --volumes >/dev/null 2>&1 || true

record_tools
record_images
write_meta
rm -rf "$R/tmp"
$REPORT build "$R" >/dev/null
status=$?
cat "$R/report.md"
exit "$status"
