#!/usr/bin/env bash
# Inside the toolbox (ops/verify/Dockerfile): start this container's own
# Docker daemon, check the commit out of the bundle run.sh mounted, and run
# every suite. Nothing outside the container is touched but /report.
set -euo pipefail

: "${VERIFY_COMMIT:?run.sh sets VERIFY_COMMIT}"
mkdir -p /report/logs

# The dind wrapper mounts the cgroups and security filesystems dockerd needs.
dind dockerd --host=unix:///var/run/docker.sock >/report/logs/dockerd.log 2>&1 &
for _ in $(seq 1 120); do
  docker info >/dev/null 2>&1 && break
  sleep 1
done
docker info >/dev/null 2>&1 || { echo "verify: the inner Docker daemon did not start" >&2; tail -50 /report/logs/dockerd.log >&2; exit 1; }

git config --global --add safe.directory '*'
git config --global advice.detachedHead false
git init -q /work/repo
git -C /work/repo fetch -q --update-head-ok /src/repo.bundle '+refs/*:refs/*'
# A detached HEAD (a CI checkout) is in the bundle as HEAD only.
git -C /work/repo fetch -q /src/repo.bundle HEAD:refs/verify/head 2>/dev/null || true
git -C /work/repo checkout -q --detach "$VERIFY_COMMIT"

cd /work/repo
exec bash /opt/verify/suites.sh
