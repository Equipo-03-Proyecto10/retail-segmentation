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
#   .github/scripts/stage-contracts.sh --all     # every services/*/ with a Dockerfile
set -euo pipefail

if [[ ${1:-} == --all ]]; then
  for dockerfile in services/*/Dockerfile; do
    [[ -f $dockerfile ]] || continue
    bash "$0" "$(basename "$(dirname "$dockerfile")")"
  done
  exit 0
fi

name=${1:?usage: $0 <name> | --all}
[[ $name =~ ^[a-z][a-z0-9-]*$ ]] || { echo "stage-contracts: bad name '$name'" >&2; exit 2; }
[[ -d contracts/$name ]] || { echo "stage-contracts: contracts/$name does not exist" >&2; exit 1; }

target=services/$name/contracts
rm -rf "$target"
mkdir -p "$target/common"
cp -R "contracts/$name" "$target/$name"
cp -R contracts/common/v1 "$target/common/v1"
