#!/usr/bin/env bash
# Fairness rule 1 of ADR-0042: before anything is measured, both builds pass
# the shared conformance suite (F13-09) and the products read cases on the
# same database, and answer a fixed sample identically. Run from the
# repository root; it leaves the bench running for the load runs (#455).
#
#   bash bench/conformance.sh
#   docker compose -f bench/compose.yaml --profile bench down -v   # afterwards
set -euo pipefail

compose=(docker compose -f bench/compose.yaml --profile bench)

bash bench/keys.sh
# The twin's build context carries its contract, as a service's does (ADR-0045).
rm -rf bench/products-service-rs/contracts
mkdir -p bench/products-service-rs/contracts/common
cp -R contracts/products bench/products-service-rs/contracts/products
cp -R contracts/common/v1 bench/products-service-rs/contracts/common/v1

"${compose[@]}" up --build --wait --wait-timeout 300

export CONFORMANCE_SERVICE=products
export CONFORMANCE_CONTRACT_DIR=contracts/products
export CONFORMANCE_PRIVATE_KEY=bench/keys/auth-bench.pem
export CONFORMANCE_KID=bench
export REDIS_OPS_PASSWORD=${REDIS_OPS_PASSWORD:-bench-ops-only}
CONFORMANCE_REDIS_STATE=$("${compose[@]}" ps -q redis-state)
CONFORMANCE_REDIS_CACHE=$("${compose[@]}" ps -q redis-cache)
export CONFORMANCE_REDIS_STATE CONFORMANCE_REDIS_CACHE

for target in products:5003:0 products-service-rs:5103:1; do
  IFS=: read -r name port twin <<<"$target"
  echo "== conformance: $name on 127.0.0.1:$port"
  CONFORMANCE_BASE_URL=http://127.0.0.1:$port BENCH_TWIN=$twin \
    python3 -m pytest tests/conformance bench/tests -q -p no:cacheprovider
done

echo "== identical answers"
python3 bench/compare.py http://127.0.0.1:5003 http://127.0.0.1:5103
