#!/usr/bin/env bash
# The products reads benchmark of ADR-0042 (F15-02): conformance, fairness
# settings, calibration, and the measured repetitions. Run from the repository
# root, on a developer machine or a CI runner, never on the instance (needs
# Docker, a Linux host with cgroup v2, and root for PSS, see BENCH_PRIV):
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
#   BENCH_CAL_PASSES=5               how many times the saturation calibration is repeated
#   BENCH_COLDSTART_RUNS=5           restarts per build for the cold start and idle memory
#   BENCH_SAMPLE_INTERVAL_SECONDS=10 RSS and PSS sampling interval inside the measured phase
#   BENCH_SAMPLE_MARGIN_SECONDS=5    CPU, RSS and PSS are read inside the measured phase, this far from each edge
#   BENCH_SETTLE_SECONDS=5           pause after each k6 run
#   BENCH_NO_BUILD=1                 compose up --no-build: the twin's image is already loaded (CI)
#   BENCH_PRIV="sudo -n"             how /proc/<pid>/smaps_rollup (PSS) is read: root is needed for another
#                                    user's processes; empty when already root
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
passes=${BENCH_CAL_PASSES:-5}
coldstarts=${BENCH_COLDSTART_RUNS:-5}
sample_interval=${BENCH_SAMPLE_INTERVAL_SECONDS:-10}
sample_margin=${BENCH_SAMPLE_MARGIN_SECONDS:-5}
settle=${BENCH_SETTLE_SECONDS:-5}
cgroup_root=${BENCH_CGROUP_ROOT:-/sys/fs/cgroup} # only a test points it elsewhere
if [[ -n ${BENCH_PRIV+x} ]]; then
  read -r -a priv <<<"$BENCH_PRIV"
elif ((EUID == 0)); then
  priv=()
else
  priv=(sudo -n)
fi
settings="reps=$reps warmup=$warmup measure=$measure fraction=$fraction step=$step passes=$passes interval=$sample_interval margin=$sample_margin"
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

# The containers' ids, fixed once: stopping and starting a container keeps its id.
declare -A cid
for build in flask rust; do cid[$build]=$("${compose[@]}" ps -q "${service[$build]}"); done
container() { echo "${cid[$1]}"; }

# CPU use and the limits come from the container's cgroup (v2), found through
# its init process so that the cgroupfs and systemd drivers both work.
cgroup_dir() {
  local pid path
  pid=$(docker inspect -f '{{.State.Pid}}' "$(container "$1")")
  [[ $pid =~ ^[1-9][0-9]*$ ]] || { echo "FAIL: ${service[$1]} is not running" >&2; return 1; }
  path=$(sed -n 's/^0:://p' "/proc/$pid/cgroup")
  [[ -n $path && -d $cgroup_root$path ]] || { echo "FAIL: no cgroup v2 directory for ${service[$1]} (/proc/$pid/cgroup)" >&2; return 1; }
  echo "$cgroup_root$path"
}
# cg <build> <file>: one cgroup file's content, `absent` when the controller is off.
cg() { cat "$(cgroup_dir "$1")/$2" 2>/dev/null | tr '\n' ' ' | sed 's/ $//' || true; }
cg_or_absent() {
  local value
  value=$(cg "$@")
  echo "${value:-absent}"
}

# cpu_stat <build>: the cgroup's cumulative CPU counters, in this order: usage_usec
# user_usec system_usec nr_periods nr_throttled throttled_usec. Reading the
# counter at the two edges of a window gives the CPU time the container used,
# kernel time included, with none of docker stats' sampling error, and the same
# counters carry the quota's throttling.
cpu_stat() {
  awk '$1 == "usage_usec" { u = $2 } $1 == "user_usec" { us = $2 } $1 == "system_usec" { sy = $2 }
       $1 == "nr_periods" { p = $2 } $1 == "nr_throttled" { t = $2 } $1 == "throttled_usec" { tu = $2 }
       END { print u + 0, us + 0, sy + 0, p + 0, t + 0, tu + 0 }' "$(cgroup_dir "$1")/cpu.stat"
}

# rss_kb <build>: the resident set of every process in the container, summed.
# Shared pages are counted once per process, so a container with a forked
# worker counts them twice; pss_kb below splits them.
rss_kb() { docker top "$(container "$1")" -eo pid,rss | awk 'NR>1{s+=$2} END{print s+0}'; }

# pss_kb <build>: the proportional set size of every process in the container,
# summed from /proc/<pid>/smaps_rollup. Reading another user's process needs
# root (BENCH_PRIV, `sudo -n` by default), so it fails loudly instead of
# reporting a partial sum.
pss_kb() {
  local pid kb total=0
  for pid in $(docker top "$(container "$1")" -eo pid | awk 'NR>1{print $1}'); do
    [[ -d /proc/$pid ]] || continue # it exited between the listing and the read
    kb=$("${priv[@]}" awk '/^Pss:/ { print $2 }' "/proc/$pid/smaps_rollup") || kb=
    if [[ ! $kb =~ ^[0-9]+$ ]]; then
      [[ -d /proc/$pid ]] || continue
      echo "FAIL: cannot read /proc/$pid/smaps_rollup (BENCH_PRIV='${priv[*]}'): PSS needs root" >&2
      return 1
    fi
    total=$((total + kb))
  done
  echo "$total"
}

# 2. Fairness settings (rules 2 to 5), read back from the running containers
#    and, for the limits, from their cgroups.
echo "== fairness settings"
[[ -f $cgroup_root/cgroup.controllers ]] ||
  { echo "FAIL: cgroup v2 is required: CPU use and the limits are read from each container's cgroup"; exit 1; }
echo "cgroup controllers at the root: $(cat "$cgroup_root/cgroup.controllers")"
declare -A readback
memlimit=
for build in flask rust; do
  id=$(container "$build")
  limits=$(docker inspect -f '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}} {{.HostConfig.CpusetCpus}}' "$id")
  mem_max=$(cg_or_absent "$build" memory.max)
  cpu_max=$(cg_or_absent "$build" cpu.max)
  # The CPUs the container's init process may run on: what the kernel enforces,
  # whether or not the cgroup exposes cpuset.cpus.effective.
  cpuset=$(awk '/^Cpus_allowed_list:/ { print $2 }' "/proc/$(docker inspect -f '{{.State.Pid}}' "$id")/status")
  if [[ $limits == "167772160 500000000 $BENCH_APP_CPUS" && $mem_max == 167772160 ]]; then
    this=160m
  elif [[ $limits == "0 500000000 $BENCH_APP_CPUS" && ${BENCH_ALLOW_NO_MEMLIMIT:-0} == 1 &&
    ($mem_max == absent || $mem_max == max) ]]; then
    # Raspberry Pi OS boots with cgroup_disable=memory: Docker discards mem_limit.
    # Both builds then run unlimited; the report states it.
    this=not-enforced
    echo "WARN: $build: the kernel has no memory cgroup, mem_limit 160m is NOT enforced"
  else
    echo "FAIL: $build: docker has '$limits' (mem nanocpus cpuset) and the cgroup memory.max is '$mem_max'; ADR-0042 needs 167772160 for both (BENCH_ALLOW_NO_MEMLIMIT=1 accepts a kernel without a memory cgroup)"
    exit 1
  fi
  [[ $cpu_max == "50000 100000" && $cpuset == "$BENCH_APP_CPUS" ]] ||
    { echo "FAIL: $build: cgroup cpu.max is '$cpu_max' and the init process may run on CPUs '$cpuset', not '50000 100000' and '$BENCH_APP_CPUS'"; exit 1; }
  [[ -z $memlimit || $memlimit == "$this" ]] || { echo "FAIL: the two builds do not have the same memory limit"; exit 1; }
  memlimit=$this
  readback[$build]="memory.max=$mem_max memory.swap.max=$(cg_or_absent "$build" memory.swap.max) cpu.max=$cpu_max cpus_allowed=$cpuset"
  echo "$build: mem $memlimit, cpus 0.5, cpuset $BENCH_APP_CPUS; cgroup read-back: ${readback[$build]}"
done
env_of() { docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$(container flask)"; }
for flag in CACHE_ENABLED=false RATE_LIMIT_ENABLED=false STREAM_CONSUMER_ENABLED=false OUTBOX_RELAY_ENABLED=false; do
  env_of | grep -qx "$flag" || { echo "FAIL: products is missing $flag"; exit 1; }
done
echo "products: CACHE_ENABLED, RATE_LIMIT_ENABLED, STREAM_CONSUMER_ENABLED, OUTBOX_RELAY_ENABLED all false"
docker inspect -f '{{.Config.Cmd}}' "$(container flask)"
echo "rust: 2 Tokio workers and 3 pooled connections are constants of the crate and kit-rs"
[[ -z $(git status --porcelain deploy/) ]] || { echo "FAIL: deploy/ is modified"; exit 1; }
for build in flask rust; do
  echo "$build at rest: rss $(rss_kb "$build") kB, pss $(pss_kb "$build") kB, cpu counters $(cpu_stat "$build")"
done
((measure > 2 * sample_margin)) || { echo "FAIL: BENCH_MEASURE_SECONDS must exceed twice BENCH_SAMPLE_MARGIN_SECONDS"; exit 1; }

# Facts the report records.
{
  echo "date_utc=$(date -u +%FT%TZ)"
  echo "commit=$(git rev-parse HEAD)"
  # The run's own output under bench/results/ is untracked by design.
  echo "dirty=$([[ -n $(git status --porcelain -- bench services contracts sql ':(exclude)bench/results') ]] && echo yes || echo no)"
  echo "cpu=$(lscpu | sed -n 's/^Model name: *//p' | head -1)"
  echo "cores=$(nproc)"
  echo "threads_per_core=$(lscpu | sed -n 's/^Thread(s) per core: *//p' | head -1)"
  echo "cpu_to_core=$(lscpu -p=CPU,CORE | grep -v '^#' | tr '\n' ' ' | sed 's/ $//')"
  echo "memory=$(free -h | awk '/^Mem:/{print $2}')"
  echo "swap=$(free -h | awk '/^Swap:/{print $2}')"
  echo "os=$(. /etc/os-release && echo "$PRETTY_NAME") kernel $(uname -r) $(uname -m)"
  echo "runner=${RUNNER_NAME:-none} image=${ImageVersion:-none}"
  echo "docker=$(docker version -f '{{.Server.Version}}')"
  echo "docker_cgroup=$(docker info -f '{{.CgroupDriver}} cgroup-v{{.CgroupVersion}}')"
  echo "cgroup_flask=${readback[flask]}"
  echo "cgroup_rust=${readback[rust]}"
  echo "k6=$(docker run --rm "$k6_image" version | head -1)"
  echo "k6_image_digest=$(docker image inspect -f '{{index .RepoDigests 0}}' "$k6_image" 2>/dev/null || true)"
  echo "products_image=$(docker inspect -f '{{.Config.Image}}' "$(container flask)") $(docker inspect -f '{{.Image}}' "$(container flask)")"
  echo "products_image_size_bytes=$(docker image inspect -f '{{.Size}}' "ghcr.io/equipo-03-proyecto10/mosaiq-products:$PRODUCTS_TAG")"
  echo "twin_image=$(docker inspect -f '{{.Config.Image}}' "$(container rust)") $(docker inspect -f '{{.Image}}' "$(container rust)")"
  echo "twin_image_size_bytes=$(docker image inspect -f '{{.Size}}' mosaiq-products-service-rs:bench)"
  echo "kit_py=$(grep -o 'kit-py/v[0-9.]*' services/products/pyproject.toml | head -1)"
  echo "kit_rs=$(grep -o 'kit-rs/v[0-9.]*' bench/products-service-rs/Cargo.toml | head -1)"
  echo "postgres=$(docker compose -f bench/compose.yaml --profile bench ps -q postgres | xargs docker inspect -f '{{.Config.Image}} {{.Image}}')"
  echo "memory_limit=$memlimit"
  echo "pss_read_with=${priv[*]:-directly}"
  echo "$settings"
  echo "cal_rates=$cal_rates"
  echo "cpus=app:$BENCH_APP_CPUS backing:$BENCH_BACKING_CPUS k6:$k6_cpus"
} >"$out/facts.txt"
cat "$out/facts.txt"

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

# sample_window <build> <sample-file> <warmup> <measure>: started beside k6, it
# reads the container inside the measured phase and writes what it saw:
# - the cgroup's CPU counters at the two edges of the window (CPU used, and
#   how often the 0.5 CPU quota throttled it);
# - the peak of the RSS and PSS sums, sampled every BENCH_SAMPLE_INTERVAL_SECONDS.
# The window starts `margin` seconds after the warm-up and ends `margin`
# seconds before the phase does: this sampler starts with `docker run`, k6 a
# moment later, so the margin keeps the window inside the measured phase.
sample_window() {
  local build=$1 file=$2 warm=$3 span=$4
  local window=$((span - 2 * sample_margin)) t0 t1 c0 c1 deadline now rss pss peak_rss=0 peak_pss=0 n=0
  local u0 us0 sy0 p0 th0 tu0 u1 us1 sy1 p1 th1 tu1
  sleep $((warm + sample_margin))
  t0=$(date +%s%N)
  c0=$(cpu_stat "$build")
  deadline=$((t0 + window * 1000000000))
  while :; do
    rss=$(rss_kb "$build")
    pss=$(pss_kb "$build")
    if ((rss > peak_rss)); then peak_rss=$rss; fi
    if ((pss > peak_pss)); then peak_pss=$pss; fi
    n=$((n + 1))
    now=$(date +%s%N)
    if ((now + sample_interval * 1000000000 >= deadline)); then break; fi
    sleep "$sample_interval"
  done
  now=$(date +%s%N)
  if ((now < deadline)); then
    sleep "$(awk -v ns=$((deadline - now)) 'BEGIN { printf "%.3f", ns / 1e9 }')"
  fi
  c1=$(cpu_stat "$build")
  t1=$(date +%s%N)
  read -r u0 us0 sy0 p0 th0 tu0 <<<"$c0"
  read -r u1 us1 sy1 p1 th1 tu1 <<<"$c1"
  {
    echo "window_s=$(awk -v ns=$((t1 - t0)) 'BEGIN { printf "%.3f", ns / 1e9 }')"
    echo "samples=$n"
    echo "rss_peak_kb=$peak_rss"
    echo "pss_peak_kb=$peak_pss"
    echo "cpu_usage_usec=$((u1 - u0))"
    echo "cpu_user_usec=$((us1 - us0))"
    echo "cpu_system_usec=$((sy1 - sy0))"
    echo "cpu_nr_periods=$((p1 - p0))"
    echo "cpu_nr_throttled=$((th1 - th0))"
    echo "cpu_throttled_usec=$((tu1 - tu0))"
  } >"$file.partial"
  mv "$file.partial" "$file"
}
trap 'jobs -p | xargs -r kill 2>/dev/null || true' EXIT

# 3. Cold start and idle memory: restart the container, time the first good answer.
echo "== cold start and idle memory"
for build in flask rust; do
  for i in $(seq 1 "$coldstarts"); do
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
    echo "cold_start_ms=$ms idle_rss_kb=$(rss_kb "$build") idle_pss_kb=$(pss_kb "$build")" >"$file"
    echo "$build $i: $(cat "$file")"
  done
done

# 4. Calibration: each build, format and scenario on a ramp of arrival-rate
#    steps, repeated `passes` times (the builds alternate between passes);
#    bench/aggregate.py applies the criterion in README.md and takes the
#    median of the passes as each build's saturation rate.
echo "== calibration, $passes passes"
for pass in $(seq 1 "$passes"); do
  if ((pass % 2)); then order=(flask rust); else order=(rust flask); fi
  for scn in $scenarios; do
    for fmt in $formats; do
      for build in "${order[@]}"; do
        file=$out/calibration/cal$pass-$build-$fmt-$scn.json
        [[ -f $file ]] && continue
        echo "calibrate $pass/$passes $build $fmt $scn"
        k6 "$build" "$scn" "$fmt" calibrate "$file" -e RATES="$cal_rates" -e STEP_SECONDS="$step"
        sleep "$settle"
      done
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
        sample=${file%.json}.sample
        [[ -f $file && -f $sample ]] && continue
        echo "rep $rep $build $fmt $scn at $rate/s"
        sample_window "$build" "$sample" "$warmup" "$measure" &
        sampler=$!
        k6 "$build" "$scn" "$fmt" measure "$file" \
          -e RATE="$rate" -e WARMUP_SECONDS="$warmup" -e MEASURE_SECONDS="$measure"
        wait "$sampler"
        sleep "$settle"
      done
    done
  done
done

# 6. The cgroups once more, after everything ran: the peak and the events
#    since the last restart show whether the limit was ever reached.
echo "== cgroup read-back after the run"
{
  for build in flask rust; do
    for f in memory.max memory.swap.max memory.current memory.peak memory.swap.current memory.events cpu.max; do
      echo "$build $f: $(cg_or_absent "$build" "$f")"
    done
  done
} | tee "$out/cgroup-end.txt"

echo "== done: $out"
echo "report tables: python3 bench/aggregate.py report $out"
[[ -n ${BENCH_KEEP_UP:-} ]] || "${compose[@]}" down -v >/dev/null
