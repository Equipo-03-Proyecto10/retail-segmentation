#!/usr/bin/env bash
# One Locust run of the platform's profiles (F29-15, #1191), with its facts.
#
#   bash bench/locust/run.sh <label>        # e.g. local-macbook-air, instance
#
# Writes bench/locust/results/<date>-<label>/ (BENCH_LOCUST_OUT overrides it):
#   facts.txt          the commit, the machine, Docker and the images measured
#   settings.txt       the run's targets, test data and load shape
#   requests.csv.gz    every request: time, class, endpoint, ms, ok, status
#   phases.csv.gz      when each step started and its user count
#   locust_*.csv.gz    Locust's own summary, failures and exceptions
#   report.md          report.py's tables
# The CSVs are gzipped because the repository ignores *.csv (AGENTS.md).
#
# Locust comes from bench/locust/requirements.txt, in a virtualenv of its own
# (LOCUST, default `locust` on PATH); it is never in the web's or a service's
# requirements. Every MOSAIQ_LOCUST_* variable of the locustfile passes
# through; see bench/locust/README.md. BENCH_LOCUST_PROJECT names the local
# compose project whose images facts.txt records (empty on the instance).
set -euo pipefail

label=${1:?usage: run.sh <label>}
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
here=$root/bench/locust
out=${BENCH_LOCUST_OUT:-$here/results/$(date -u +%Y-%m-%d)-$label}
locust=${LOCUST:-locust}
project=${BENCH_LOCUST_PROJECT-p1191}

if [[ -e $out/report.md ]]; then
  echo "run.sh: $out already holds a run" >&2
  exit 1
fi
mkdir -p "$out"

{
  echo "label=$label"
  echo "date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "commit=$(git -C "$root" rev-parse HEAD)"
  echo "dirty=$(git -C "$root" status --porcelain --untracked-files=no | wc -l | tr -d ' ')"
  # The load test's own files, untracked ones included: they are the run.
  echo "locust_files_dirty=$(git -C "$root" status --porcelain -- bench/locust ':!bench/locust/results' | wc -l | tr -d ' ')"
  echo "locustfile_sha256=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$here/locustfile.py")"
  echo "locust=$("$locust" --version 2>/dev/null | head -1 | awk '{print $2}')"
  [[ -n ${MOSAIQ_LOCUST_ACCOUNTS_FILE:-} ]] &&
    echo "account_pools=$(python3 -c 'import json,sys; print({k: len(v) for k, v in json.load(open(sys.argv[1])).items()})' "$MOSAIQ_LOCUST_ACCOUNTS_FILE")"
  echo "host_os=$(uname -srm)"
  if [[ $(uname -s) == Darwin ]]; then
    echo "host_model=$(sysctl -n hw.model)"
    echo "host_cpu=$(sysctl -n machdep.cpu.brand_string)"
    echo "host_cpus=$(sysctl -n hw.ncpu)"
    echo "host_memory_bytes=$(sysctl -n hw.memsize)"
  else
    echo "host_cpu=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | xargs)"
    echo "host_cpus=$(nproc)"
    echo "host_memory_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')"
  fi
  if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    echo "docker_cpus=$(docker info --format '{{.NCPU}}')"
    echo "docker_memory_bytes=$(docker info --format '{{.MemTotal}}')"
    if [[ -n $project ]]; then
      docker ps --filter "label=com.docker.compose.project=$project" \
        --format '{{.Names}}' | sort | while read -r name; do
        image=$(docker inspect --format '{{.Config.Image}}' "$name")
        id=$(docker inspect --format '{{.Image}}' "$name" | cut -c8-19)
        limits=$(docker inspect --format '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}' "$name")
        # The document store's container is named after its engine, which
        # guards.sh (ADR-0038) refuses outside engagement; its image says it.
        [[ $image == mongo:* ]] && name=$project-document-store
        echo "container=$name image=$image id=$id memory_bytes_nanocpus=$limits"
      done
    fi
  fi
  for version in "$root"/services/*/VERSION; do
    echo "version_$(basename "$(dirname "$version")")=$(cat "$version")"
  done
} >"$out/facts.txt"

# Locust exits 1 when any request failed; that is a result, not a stop.
status=0
MOSAIQ_LOCUST_OUT=$out "$locust" -f "$here/locustfile.py" --headless --only-summary \
  --csv "$out/locust" --loglevel WARNING >"$out/locust-summary.txt" 2>&1 || status=$?
cat "$out/locust-summary.txt"
# A run that never reached its first step (a refused sign-in, say) is no run.
if [[ $status -gt 1 ]] || ! grep -q ',step-1,' "$out/phases.csv"; then
  echo "run.sh: locust stopped with status $status" >&2
  exit 1
fi

python3 "$here/report.py" "$out" >"$out/report.md"
rm -f "$out/locust_stats_history.csv"
gzip -9 -f "$out"/*.csv
echo "run.sh: wrote $out"
