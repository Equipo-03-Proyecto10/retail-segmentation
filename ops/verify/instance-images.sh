#!/usr/bin/env bash
# The images the instance runs (F20-12, #784).
#
# Run on the instance, as a user that can read Docker. It prints one tab-
# separated line per running mosaiq container, to be passed to
# `publish.py --instance-images`:
#
#   container  image-reference  registry-digest  revision  version
#
# `registry-digest` is the digest the image was pulled by (RepoDigests).
# `revision` and `version` are the image's own OCI labels, which deploy.sh
# already requires to be the merged commit (deploy/README.md). An empty field
# is printed empty, never filled in.
set -euo pipefail

docker ps --filter name=mosaiq --format '{{.Names}}' | sort | while read -r name; do
  docker inspect --format \
    '{{.Name}}{{"\t"}}{{.Config.Image}}{{"\t"}}{{if .Image}}{{.Image}}{{end}}{{"\t"}}{{index .Config.Labels "org.opencontainers.image.revision"}}{{"\t"}}{{index .Config.Labels "org.opencontainers.image.version"}}' \
    "$name" | while IFS=$'\t' read -r cname ref image_id revision version; do
    digest=$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "$image_id" 2>/dev/null || true)
    digest=${digest#*@}
    printf '%s\t%s\t%s\t%s\t%s\n' "${cname#/}" "$ref" "$digest" "$revision" "$version"
  done
done
