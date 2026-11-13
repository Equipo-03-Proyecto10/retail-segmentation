#!/usr/bin/env bash
# Copy one service's contract and the common schema into its build context
# (ADR-0040, ADR-0045), where its Dockerfile copies them from:
#
#   services/<name>/contracts/<name>/       from contracts/<name>/
#   services/<name>/contracts/common/v1/    from contracts/common/v1/
#
# The directory is git-ignored; the image serves these files byte for byte.
#
#   .github/scripts/stage-contracts.sh <name>
#   .github/scripts/stage-contracts.sh --all             # every services/*/ with a Dockerfile
#   .github/scripts/stage-contracts.sh --sources <name>  # the paths it copies, one per line
#
# ci-images.sh keys an image by --sources (ADR-0080), so the paths staged and
# the paths keyed come from the one list in sources().
set -euo pipefail

sources() { printf '%s\n' "contracts/$1" contracts/common/v1; }

if [[ ${1:-} == --all ]]; then
  for dockerfile in services/*/Dockerfile; do
    [[ -f $dockerfile ]] || continue
    bash "$0" "$(basename "$(dirname "$dockerfile")")"
  done
  exit 0
fi

mode=stage
if [[ ${1:-} == --sources ]]; then
  mode=sources
  shift
fi
name=${1:?usage: $0 <name> | --all | --sources <name>}
[[ $name =~ ^[a-z][a-z0-9-]*$ ]] || { echo "stage-contracts: bad name '$name'" >&2; exit 2; }
if [[ $mode == sources ]]; then
  sources "$name"
  exit 0
fi
[[ -d contracts/$name ]] || { echo "stage-contracts: contracts/$name does not exist" >&2; exit 1; }

target=services/$name/contracts
rm -rf "$target"
while IFS= read -r source; do
  # contracts/<name> lands at contracts/<name>, contracts/common/v1 at contracts/common/v1.
  mkdir -p "$(dirname "$target/${source#contracts/}")"
  cp -R "$source" "$target/${source#contracts/}"
done < <(sources "$name")
