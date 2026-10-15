#!/usr/bin/env bash
set -euo pipefail

while IFS= read -r stream; do
  body=${stream#stream:}
  producer=${body%%.v*}
  version_and_event=${body#*.v}
  version=${version_and_event%%.*}
  event=${version_and_event#*.}
  contract="contracts/streams/$producer.$event.v$version.md"
  if [[ ! -f $contract ]]; then
    printf 'FAIL: %s has no contract (%s)\n' "$stream" "$contract"
  fi
done < <(grep -oE 'stream:[a-z_]+\.v[0-9]+\.[a-z_]+' sql/01_schema.sql | sort -u || true)
