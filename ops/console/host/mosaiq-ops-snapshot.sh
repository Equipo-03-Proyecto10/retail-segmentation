#!/usr/bin/env bash
# Write the container snapshot ops-console reads (ADR-0055), so the console
# never needs the Docker socket. Run by mosaiq-ops-snapshot.timer as root.
#
#   ops/console/host/mosaiq-ops-snapshot.sh [OUT]
#
# OUT defaults to /run/mosaiq/ops/containers.json. The file is replaced with a
# rename, so a reader never sees half of it; the console mounts the directory,
# not the file, because a file bind mount would keep the replaced inode.
set -euo pipefail

out=${1:-/run/mosaiq/ops/containers.json}
dir=$(dirname "$out")
mkdir -p "$dir"

# `{{json .}}` prints one object per line; join them into an array.
array() { paste -sd, - | sed 's/^/[/; s/$/]/'; }

ps=$(docker ps -a --filter name=mosaiq- --format '{{json .}}' | array)
running=$(docker ps -q --filter name=mosaiq-)
if [[ -n $running ]]; then
  # shellcheck disable=SC2086  # one argument per container id
  stats=$(docker stats --no-stream --format '{{json .}}' $running | array)
else
  stats='[]'
fi
ids=$(docker ps -aq --filter name=mosaiq-)
if [[ -n $ids ]]; then
  # shellcheck disable=SC2086
  restarts=$(docker inspect --format '"{{slice .Name 1}}":{{.RestartCount}}' $ids \
    | paste -sd, - | sed 's/^/{/; s/$/}/')
else
  restarts='{}'
fi

tmp=$(mktemp "$dir/.containers.json.XXXXXX")
trap 'rm -f "$tmp"' EXIT
printf '{"taken_at":%s,"ps":%s,"stats":%s,"restarts":%s}\n' \
  "$(date +%s)" "${ps:-[]}" "$stats" "$restarts" >"$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$out"
