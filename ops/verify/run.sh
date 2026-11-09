#!/usr/bin/env bash
# Run every test suite against one commit, from an empty environment, with one
# command (F20-03, #761). ops/verify/README.md describes the run and its report.
#
#   bash ops/verify/run.sh [--out <dir>] [<commit>]       # default HEAD
#   bash ops/verify/run.sh --compare <dir-a> <dir-b>
#
# The host needs Docker and this git checkout, nothing else. The toolbox image
# (ops/verify/Dockerfile) starts its own Docker daemon, checks the commit out
# of a bundle of this repository, and builds and runs everything on that
# daemon, so no container, network, volume or port of the host is used, and
# two runs never share a cache. The report lands in <dir>, by default
# test-results/verify/<commit>-<UTC time>/. The exit status is the report's:
# non-zero when any case failed or any suite could not run.
#
# VERIFY_BUILD_SERIAL=1 builds the service images one at a time, for a machine
# with little memory.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(git -C "$here" rev-parse --show-toplevel)

if [[ ${1:-} == --compare ]]; then
  [[ $# -eq 3 ]] || { echo "usage: $0 --compare <dir-a> <dir-b>" >&2; exit 2; }
  docker run --rm -v "$here:/opt/verify:ro" -v "$(cd "$2" && pwd):/a" -v "$(cd "$3" && pwd):/b" \
    -v "$(cd "$3" && pwd):/out" python:3.12-slim \
    python3 /opt/verify/report.py compare /a/report.json /b/report.json /out/comparison.md
  exit
fi

out=
if [[ ${1:-} == --out ]]; then
  out=${2:?usage: $0 --out <dir> [<commit>]}
  shift 2
fi
commit=$(git -C "$root" rev-parse --verify "${1:-HEAD}^{commit}")
out=${out:-$root/test-results/verify/$commit-$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$out"
out=$(cd "$out" && pwd)

work=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/verify.XXXXXX")
name=verify-${commit:0:12}-$(basename "$work" | cut -d. -f2 | tr 'A-Z' 'a-z')
cleanup() {
  docker rm -fv "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

# Every ref and HEAD, so check.sh finds origin/develop and a detached commit
# is still reachable.
git -C "$root" bundle create "$work/repo.bundle" --all HEAD 2>/dev/null

echo "verify: building the toolbox image"
docker build --platform linux/amd64 -q -t mosaiq-verify:local "$here" >/dev/null
toolbox=$(docker image inspect -f '{{.Id}}' mosaiq-verify:local)

echo "verify: running every suite against $commit; report in $out"
status=0
# Privileged for the daemon inside; its storage is an anonymous volume that
# goes with the container.
docker run --name "$name" --rm --privileged --platform linux/amd64 \
  -v /var/lib/docker \
  -v "$work:/src:ro" \
  -v "$out:/report" \
  -e VERIFY_COMMIT="$commit" \
  -e VERIFY_TOOLBOX="$toolbox" \
  -e VERIFY_RUNNER_COMMIT="$(git -C "$root" rev-parse HEAD)" \
  -e VERIFY_BUILD_SERIAL="${VERIFY_BUILD_SERIAL:-0}" \
  -e GITHUB_ACTIONS="${GITHUB_ACTIONS:-}" \
  mosaiq-verify:local || status=$?

if [[ ! -f $out/report.json ]]; then
  echo "verify: the run ended (exit $status) before it wrote a report; see $out/logs/" >&2
  exit "$((status ? status : 1))"
fi
exit "$status"
