#!/usr/bin/env bash
# Run propagation.py where Redis and the PostgreSQL socket are reachable: in a
# throwaway container on the services' network (F20-06, #764).
#
#   ops/propagation/run.sh measure [--check] [propagation.py measure options]
#   ops/propagation/run.sh backlog STOP_CMD START_CMD [propagation.py backlog options]
#
# Run from the repository root. Reports are written to $PROPAGATION_OUT
# (default: a new temporary directory, printed at the end).
#
# Environment (defaults are the instance's; the README gives the laptop's):
#   DATABASE_URL        a role that may update the captured tables and read
#                       every <suffix>_events.outbox      required
#   REDIS_URL           redis-state, read-only user ops_console is enough
#                                                        required
#   PROPAGATION_NETWORK the services' Docker network      mosaiq
#   PROPAGATION_PG_SOCKET host directory or volume holding the socket
#                                                        /run/postgresql
#   PROPAGATION_IMAGE   the Python image                  python:3.12.12-slim
#   PROPAGATION_ENV     the report's environment label    instance
#
# backlog stops the consumer with STOP_CMD, lets the container raise and relay
# the backlog, starts the consumer with START_CMD and signals the container,
# which times the catch-up. The two clocks never meet: the container starts
# timing when it sees the signal file.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${REDIS_URL:?REDIS_URL is required}"
network=${PROPAGATION_NETWORK:-mosaiq}
socket=${PROPAGATION_PG_SOCKET:-/run/postgresql}
image=${PROPAGATION_IMAGE:-python:3.12.12-slim}
environment=${PROPAGATION_ENV:-instance}
out=${PROPAGATION_OUT:-$(mktemp -d)}
mkdir -p "$out"
out=$(cd "$out" && pwd)

command=${1:?usage: run.sh measure|backlog ...}
shift

container=(
  docker run --rm --network "$network"
  -v "$socket:/run/postgresql"
  -v "$PWD/ops/propagation:/tool:ro"
  -v "$PWD/contracts/streams:/contracts:ro"
  -v "$out:/out"
  -e DATABASE_URL -e REDIS_URL
  "$image" sh -c
  'pip install --quiet --no-cache-dir --disable-pip-version-check --root-user-action=ignore -r /tool/requirements.txt \
     && exec python /tool/propagation.py "$@"' propagation
)

case $command in
  measure)
    "${container[@]}" measure --contracts /contracts --environment "$environment" \
      --out /out/measure.json "$@"
    ;;
  backlog)
    stop=${1:?STOP_CMD is required}
    start=${2:?START_CMD is required}
    shift 2
    rm -f "$out/ready" "$out/go"
    bash -c "$stop"
    "${container[@]}" backlog --contracts /contracts --environment "$environment" \
      --ready-file /out/ready --go-file /out/go --out /out/backlog.json "$@" &
    tool=$!
    while [[ ! -f $out/ready ]]; do
      kill -0 "$tool" 2>/dev/null || { wait "$tool"; exit 1; }
      sleep 0.05
    done
    bash -c "$start"
    touch "$out/go"
    wait "$tool"
    ;;
  *)
    echo "usage: run.sh measure|backlog ..." >&2
    exit 2
    ;;
esac
echo "reports in $out" >&2
