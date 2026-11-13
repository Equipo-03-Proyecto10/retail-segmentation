#!/usr/bin/env bash
# The fifteen service images of the local stack, for the nightly chain and the
# emulator job (ADR-0080). An image is keyed by the git trees of
# services/<name>/ and of what stage-contracts.sh stages into it, and stored as
# ghcr.io/equipo-03-proyecto10/mosaiq-ci-<name>:<key>, apart from the release
# packages. Nothing that deploys reads these images.
#
#   .github/scripts/ci-images.sh key <name> [<rev>]   # print the key, at HEAD by default
#   .github/scripts/ci-images.sh prepare              # pull each image whose key exists, build the rest
#   .github/scripts/ci-images.sh push                 # push what prepare built, never over a tag
#
# prepare tags every image as compose.distributed.yaml names it
# (mosaiq-<name>:$IMAGE_TAG from docker/distributed/local.env), so
# `docker compose up --no-build` finds all fifteen. It builds exactly what
# compose would: each service's compose.yaml builds context "." with no
# arguments. It writes one line per service to $GITHUB_STEP_SUMMARY, pulled or
# built, with the key and the commit the image was built from. Run from the
# repository root, after stage-contracts.sh --all and `docker login ghcr.io`.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
registry=ghcr.io/equipo-03-proyecto10
source_url=https://github.com/Equipo-03-Proyecto10/retail-segmentation
state=${CI_IMAGES_STATE:-${RUNNER_TEMP:-/tmp}/ci-images-built.tsv}
summary=${GITHUB_STEP_SUMMARY:-/dev/null}

key() {
  local name=$1 rev=${2:-HEAD} path tree lines=
  [[ $name =~ ^[a-z][a-z0-9-]*$ ]] || { echo "ci-images: bad name '$name'" >&2; return 2; }
  while IFS= read -r path; do
    tree=$(git rev-parse --verify --quiet "$rev:$path") \
      || { echo "ci-images: $path does not exist at $rev" >&2; return 1; }
    lines+="$path $tree"$'\n'
  done < <(
    echo "services/$name"
    bash "$here/stage-contracts.sh" --sources "$name"
  )
  printf '%s' "$lines" | sha256sum | cut -c1-24
}

prepare() {
  local tag sha dockerfile name k ci local_ref how from out
  tag=$(sed -n 's/^IMAGE_TAG=//p' docker/distributed/local.env)
  [[ -n $tag ]] || { echo "ci-images: no IMAGE_TAG in docker/distributed/local.env" >&2; return 1; }
  sha=$(git rev-parse HEAD)
  : >"$state"
  {
    echo "### Service images (ADR-0080)"
    echo
    echo "| Service | Image | Key | Built from |"
    echo "|---|---|---|---|"
  } >>"$summary"
  for dockerfile in services/*/Dockerfile; do
    name=$(basename "$(dirname "$dockerfile")")
    k=$(key "$name")
    ci=$registry/mosaiq-ci-$name:$k
    local_ref=$registry/mosaiq-$name:$tag
    if out=$(docker pull --quiet "$ci" 2>&1); then
      docker tag "$ci" "$local_ref"
      how=pulled
    else
      # Usually "not found" for a key not built yet. A refused login shows here too.
      echo "ci-images: no $ci (${out##*$'\n'}); building it" >&2
      docker build \
        --label "org.opencontainers.image.revision=$sha" \
        --label "org.opencontainers.image.source=$source_url" \
        -t "$local_ref" -t "$ci" "services/$name"
      printf '%s\t%s\n' "$name" "$ci" >>"$state"
      how=built
    fi
    from=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$local_ref")
    printf '| %s | %s | `%s` | %s |\n' "$name" "$how" "$k" "${from:-unknown}" >>"$summary"
    echo "$how $name $k"
  done
}

push() {
  local name ci
  [[ -s $state ]] || { echo "ci-images: nothing was built, nothing to push"; return 0; }
  while IFS=$'\t' read -r name ci; do
    # A key names one content; another run may have pushed it meanwhile.
    if docker manifest inspect "$ci" >/dev/null 2>&1; then
      echo "kept $ci: the tag already exists"
      continue
    fi
    docker push --quiet "$ci" >/dev/null
    echo "pushed $ci"
  done <"$state"
}

case ${1:-} in
  key) key "${2:?usage: $0 key <name> [<rev>]}" "${3:-HEAD}" ;;
  prepare) prepare ;;
  push) push ;;
  *) echo "usage: $0 key <name> [<rev>] | prepare | push" >&2; exit 2 ;;
esac
