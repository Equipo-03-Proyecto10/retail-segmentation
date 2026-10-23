#!/usr/bin/env bash
# The products reads benchmark of ADR-0042 (F15-02): conformance, fairness
# settings, calibration, and the measured repetitions. Run from the repository
# root, on a developer machine, never on the instance:
#
#   source .venv/bin/activate        # the conformance suite needs pytest
#   bash bench/run.sh
#   python3 bench/aggregate.py report bench/results/<run>   # the report's tables
#
# Everything is overridable, the defaults are the specified run:
#   BENCH_REPS=5  BENCH_WARMUP_SECONDS=30  BENCH_MEASURE_SECONDS=120
#   BENCH_RATE_FRACTION=70   percent of the lower saturation rate
#   BENCH_CAL_RATES=...      calibration steps (requests/s), BENCH_CAL_STEP_SECONDS=10
#   BENCH_SCENARIOS="list search filter detail"  BENCH_FORMATS="json xml"
#   BENCH_APP_CPUS=1 BENCH_BACKING_CPUS=2 BENCH_K6_CPUS=3   (CPU 0 is the OS's)
#   BENCH_OUT=bench/results/<name>   re-running with the same name resumes
#   BENCH_ALLOW_NO_MEMLIMIT=1        accept a kernel without a memory cgroup (mem_limit discarded)
#   BENCH_COLDSTART_TIMEOUT_SECONDS=120   how long a restarted build may take to answer 200
#   BENCH_KEEP_UP=1                  leave the stack running afterwards
#   K6_IMAGE=grafana/k6@sha256:...   the load generator, by default the reported run's (k6 v2.3.0)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

reps=${BENCH_REPS:-5}
warmup=${BENCH_WARMUP_SECONDS:-30}
measure=${BENCH_MEASURE_SECONDS:-120}
fraction=${BENCH_RATE_FRACTION:-70}
step=${BENCH_CAL_STEP_SECONDS:-10}
cal_rates=${BENCH_CAL_RATES:-10,20,40,60,80,120,160,240,320,480,640,960,1280,1920}
scenarios=${BENCH_SCENARIOS:-"list search filter detail"}
formats=${BENCH_FORMATS:-"json xml"}
export BENCH_APP_CPUS=${BENCH_APP_CPUS:-1}
export BENCH_BACKING_CPUS=${BENCH_BACKING_CPUS:-2}
k6_cpus=${BENCH_K6_CPUS:-3}
export AUTH_TAG=${AUTH_TAG:-$(cat services/auth/VERSION)}
export PRODUCTS_TAG=${PRODUCTS_TAG:-$(cat services/products/VERSION)}
coldstart_timeout=${BENCH_COLDSTART_TIMEOUT_SECONDS:-120}
# Pinned by digest: the multi-arch index `grafana/k6:latest` resolved to on
# 2026-10-22, k6 v2.3.0, so a rerun uses the reported run's load generator.
k6_image=${K6_IMAGE:-grafana/k6@sha256:e66db15b860113878fa74670e31f5e274830b7b6e42c8bff28b2f2d86a257603}
out=${BENCH_OUT:-bench/results/$(date -u +%Y%m%dT%H%M%SZ)}
out=${out%/}
settings="reps=$reps warmup=$warmup measure=$measure fraction=$fraction step=$step"
# Resuming keeps the summaries already taken, and aggregate.py reads these
# settings back from facts.txt to interpret them, so they must not change.
if [[ -f $out/facts.txt ]] &&
  ! { grep -qxF "$settings" "$out/facts.txt" && grep -qxF "cal_rates=$cal_rates" "$out/facts.txt"; }; then
  echo "FAIL: $out was started with other settings (see its facts.txt); use a new BENCH_OUT"
  exit 1
fi
mkdir -p "$out/calibration" "$out/runs" "$out/coldstart"
chmod 777 "$out/calibration" "$out/runs"

export BENCH_COMPOSE_EXTRA=bench/compose.pinned.yaml
compose=(docker compose -f bench/compose.yaml -f "$BENCH_COMPOSE_EXTRA" --profile bench)

declare -A url=([flask]=http://127.0.0.1:5003 [rust]=http://127.0.0.1:5103)
declare -A service=([flask]=products [rust]=products-service-rs)

# A fresh stack every run: a clean database, empty Redis, nothing left over.
"${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true

# 1. Conformance first (rule 1). `set -e` and the pipe's pipefail abort here
#    when either build fails, before anything is measured.
echo "== conformance (output kept in $out/conformance.txt)"
bash bench/conformance.sh 2>&1 | tee "$out/conformance.txt"

# The shared env conformance.sh exports for the token helper.
export CONFORMANCE_PRIVATE_KEY=bench/keys/auth-bench.pem CONFORMANCE_KID=bench
export REDIS_OPS_PASSWORD=${REDIS_OPS_PASSWORD:-bench-ops-only}
CONFORMANCE_REDIS_STATE=$("${compose[@]}" ps -q redis-state)
export CONFORMANCE_REDIS_STATE

container() { "${compose[@]}" ps -q "${service[$1]}"; }

# 2. Fairness settings (rules 2 to 5), read back from the running containers.
echo "== fairness settings"
for build in flask rust; do
  id=$(container "$build")
  limits=$(docker inspect -f '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}} {{.HostConfig.CpusetCpus}}' "$id")
  if [[ $limits == "0 500000000 $BENCH_APP_CPUS" && ${BENCH_ALLOW_NO_MEMLIMIT:-0} == 1 ]]; then
    # Raspberry Pi OS boots with cgroup_disable=memory: Docker discards mem_limit.
    # Both builds then run unlimited; the report states it.
    memlimit=not-enforced
    echo "WARN: $build: the kernel has no memory cgroup, mem_limit 160m is NOT enforced"
  elif [[ $limits == "167772160 500000000 $BENCH_APP_CPUS" ]]; then
    memlimit=160m
  else
    echo "FAIL: $build limits are '$limits' (mem nanocpus cpuset); BENCH_ALLOW_NO_MEMLIMIT=1 accepts a kernel without a memory cgroup"
    exit 1
  fi
  echo "$build: mem $memlimit, cpus 0.5, cpuset $BENCH_APP_CPUS"
done
env_of() { docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$(container flask)"; }
for flag in CACHE_ENABLED=false RATE_LIMIT_ENABLED=false STREAM_CONSUMER_ENABLED=false OUTBOX_RELAY_ENABLED=false; do
  env_of | grep -qx "$flag" || { echo "FAIL: products is missing $flag"; exit 1; }
done
echo "products: CACHE_ENABLED, RATE_LIMIT_ENABLED, STREAM_CONSUMER_ENABLED, OUTBOX_RELAY_ENABLED all false"
docker inspect -f '{{.Config.Cmd}}' "$(container flask)"
echo "rust: 2 Tokio workers and 3 pooled connections are constants of the crate and kit-rs"
[[ -z $(git status --porcelain deploy/) ]] || { echo "FAIL: deploy/ is modified"; exit 1; }

# Facts the report records.
{
  echo "date_utc=$(date -u +%FT%TZ)"
  echo "commit=$(git rev-parse HEAD)"
  # The run's own output under bench/results/ is untracked by design.
  echo "dirty=$([[ -n $(git status --porcelain -- bench services contracts sql ':(exclude)bench/results') ]] && echo yes || echo no)"
  echo "cpu=$(lscpu | sed -n 's/^Model name: *//p' | head -1)"
  echo "cores=$(nproc)"
  echo "memory=$(free -h | awk '/^Mem:/{print $2}')"
  echo "os=$(. /etc/os-release && echo "$PRETTY_NAME") kernel $(uname -r) $(uname -m)"
  echo "docker=$(docker version -f '{{.Server.Version}}')"
  echo "k6=$(docker run --rm "$k6_image" version | head -1)"
  echo "k6_image_digest=$(docker image inspect -f '{{index .RepoDigests 0}}' "$k6_image" 2>/dev/null || true)"
  echo "products_image=$(docker inspect -f '{{.Config.Image}}' "$(container flask)") $(docker inspect -f '{{.Image}}' "$(container flask)")"
  echo "products_image_size_bytes=$(docker image inspect -f '{{.Size}}' "ghcr.io/equipo-03-proyecto10/mosaiq-products:$PRODUCTS_TAG")"
  echo "twin_image=$(docker inspect -f '{{.Config.Image}}' "$(container rust)") $(docker inspect -f '{{.Image}}' "$(container rust)")"
  echo "twin_image_size_bytes=$(docker image inspect -f '{{.Size}}' mosaiq-products-service-rs:bench)"
  echo "kit_py=$(grep -o 'kit-py/v[0-9.]*' services/products/pyproject.toml | head -1)"
  echo "kit_rs=$(grep -o 'kit-rs/v[0-9.]*' bench/products-service-rs/Cargo.toml | head -1)"
  echo "postgres=$(docker compose -f bench/compose.yaml --profile bench ps -q postgres | xargs docker inspect -f '{{.Config.Image}} {{.Image}}')"
  echo "memory_limit=${memlimit:-unknown}"
  echo "$settings"
  echo "cal_rates=$cal_rates"
  echo "cpus=app:$BENCH_APP_CPUS backing:$BENCH_BACKING_CPUS k6:$k6_cpus"
} >"$out/facts.txt"
cat "$out/facts.txt"

# rss_kb <build>: the resident set of every process in the container, summed.
rss_kb() { docker top "$(container "$1")" -eo pid,rss | awk 'NR>1{s+=$2} END{print s+0}'; }

token() { python3 bench/mint_token.py; }

# k6 <build> <scenario> <format> <mode> <summary-file> [extra -e args...]
# The summary is written under a temporary name and kept only when k6 finished
# and the build is still running afterwards, so an interrupted run or a crashed
# build never leaves a summary that a resumed run would skip over.
k6() {
  local build=$1 scn=$2 fmt=$3 mode=$4 file=$5 dir
  shift 5
  dir=$(cd "$(dirname "$file")" && pwd)
  docker run --rm --network host --cpuset-cpus "$k6_cpus" --user "$(id -u):$(id -g)" \
    -v "$PWD/bench/k6:/scripts:ro" -v "$dir:/out" \
    -e BASE_URL="${url[$build]}" -e TOKEN="$(token)" -e FORMAT="$fmt" -e MODE="$mode" \
    -e SUMMARY_PATH="/out/$(basename "$file").partial" "$@" \
    "$k6_image" run --quiet --no-usage-report /scripts/"$scn".js >/dev/null
  [[ $(docker inspect -f '{{.State.Running}}' "$(container "$build")" 2>/dev/null) == true ]] ||
    { echo "FAIL: ${service[$build]} is no longer running after $scn $fmt $mode"; exit 1; }
  mv "$file.partial" "$file"
}

# 3. Cold start and idle memory: restart the container, time the first good answer.
echo "== cold start and idle RSS"
for build in flask rust; do
  for i in 1 2 3 4 5; do
    file=$out/coldstart/$build-$i.txt
    [[ -f $file ]] && continue
    id=$(container "$build")
    docker stop -t 5 "$id" >/dev/null
    tok=$(token)
    start=$(date +%s%N)
    docker start "$id" >/dev/null
    until [[ $(curl -s --max-time 2 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $tok" \
      -H 'Accept: application/json' "${url[$build]}/v1/products/100001" || true) == 200 ]]; do
      if (($(date +%s%N) - start > coldstart_timeout * 1000000000)); then
        echo "FAIL: $build did not answer 200 within $coldstart_timeout s of docker start"
        exit 1
      fi
      sleep 0.05
    done
    ms=$((($(date +%s%N) - start) / 1000000))
    sleep 10
    echo "cold_start_ms=$ms idle_rss_kb=$(rss_kb "$build")" >"$file"
    echo "$build $i: $(cat "$file")"
  done
done

# 4. Calibration: each build, format and scenario on a ramp of arrival-rate
#    steps; bench/aggregate.py applies the criterion in README.md.
echo "== calibration"
for scn in $scenarios; do
  for fmt in $formats; do
    for build in flask rust; do
      file=$out/calibration/$build-$fmt-$scn.json
      [[ -f $file ]] && continue
      echo "calibrate $build $fmt $scn"
      k6 "$build" "$scn" "$fmt" calibrate "$file" -e RATES="$cal_rates" -e STEP_SECONDS="$step"
      sleep 5
    done
  done
done
python3 bench/aggregate.py rates "$out" "$fraction" | tee "$out/rates.txt"

# 5. The measured runs: constant arrival at the chosen rate, warm-up then
#    measured; the builds alternate within a repetition, and the starting build
#    alternates between repetitions.
echo "== measured runs"
for rep in $(seq 1 "$reps"); do
  if ((rep % 2)); then order=(flask rust); else order=(rust flask); fi
  for scn in $scenarios; do
    for fmt in $formats; do
      rate=$(python3 bench/aggregate.py rate "$out" "$fmt" "$scn")
      for build in "${order[@]}"; do
        file=$out/runs/rep$rep-$build-$fmt-$scn.json
        [[ -f $file ]] && continue
        echo "rep $rep $build $fmt $scn at $rate/s"
        sampler=$out/runs/rep$rep-$build-$fmt-$scn.rss
        (
          sleep "$warmup"
          peak=0
          for _ in $(seq 1 $((measure / 10))); do
            now=$(rss_kb "$build")
            ((now > peak)) && peak=$now
            sleep 10
          done
          echo "$peak" >"$sampler"
        ) &
        k6 "$build" "$scn" "$fmt" measure "$file" \
          -e RATE="$rate" -e WARMUP_SECONDS="$warmup" -e MEASURE_SECONDS="$measure"
        wait
        sleep 5
      done
    done
  done
done

echo "== done: $out"
echo "report tables: python3 bench/aggregate.py report $out"
[[ -n ${BENCH_KEEP_UP:-} ]] || "${compose[@]}" down -v >/dev/null
