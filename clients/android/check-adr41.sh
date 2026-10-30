#!/usr/bin/env bash
# ADR-0041's Android checks, as its Compliance section writes them. Run from
# the repository root; prints nothing when they hold, one line per problem.
#
#   bash clients/android/check-adr41.sh
#
# The app's own sources only: app/src/test/.../SourceGuardTest.kt names the
# forbidden XML types in order to look for them.
set -uo pipefail

main=clients/android/app/src/main
registry=contracts/registry.yaml

# Android never touches XML.
grep -rlE "application/xml|XmlPullParser|SimpleXml" "$main" --include='*.kt' \
  | sed 's/^/Android touches XML: /'

# Every api/<name>/ the app calls is a service the registry names.
registered=$(sed -nE 's/^  - name: ([a-z][a-z0-9-]*)$/\1/p' "$registry")
[ -n "$registered" ] || echo "no service names read from $registry"
grep -rhoE "api/[a-z][a-z0-9-]*/v[0-9]+" "$main" --include='*.kt' | cut -d/ -f2 | sort -u \
  | while IFS= read -r name; do
    grep -qx "$name" <<<"$registered" || echo "client calls unregistered service: $name"
  done

# Retry ids are generated once, never per attempt: no UUID generation in a
# WorkManager worker.
grep -rln "UUID.randomUUID" clients/android/ --include='*Worker*.kt' \
  | sed 's/^/retry id generated per attempt: /'

# ADR-0057: color, type size and shape values live in ui/theme/ and nowhere else.
grep -rnE "Color\(0x|[0-9]\.?[0-9]*\.sp\b|RoundedCornerShape\([^)]*[0-9]\.?[0-9]*\.dp" "$main" --include='*.kt' \
  | grep -v '/ui/theme/' | sed 's/^/outside ui\/theme: /'

exit 0
