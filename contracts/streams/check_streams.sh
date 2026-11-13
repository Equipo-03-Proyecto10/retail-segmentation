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

# engagement's stream is relayed from MongoDB (ADR-0075), so its name is not in
# sql/01_schema.sql. Its contract must exist, name engagement as the producer,
# and list its consumer groups.
engagement=contracts/streams/engagement.interaction_recorded.v1.md
if [[ ! -f $engagement ]]; then
  printf 'FAIL: stream:engagement.v1.interaction_recorded has no contract (%s)\n' "$engagement"
else
  grep -qE '^\| Producer \| engagement \|$' "$engagement" \
    || printf 'FAIL: %s does not name engagement as its only producer\n' "$engagement"
  grep -qE '^\| Consumer groups \| `[a-z][a-z0-9-]*`' "$engagement" \
    || printf 'FAIL: %s lists no consumer group\n' "$engagement"
fi
