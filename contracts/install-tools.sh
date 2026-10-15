#!/usr/bin/env bash
set -euo pipefail

JQ_VERSION=1.8.2
JQ_SHA256=b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f
OASDIFF_VERSION=1.33.0
OASDIFF_SHA256=43a4e328e2d13ba1552d760aa68d2485c75c5621f309f6ff64ae895188345247

tool_tmp=$(mktemp -d)
trap 'rm -rf "$tool_tmp"' EXIT

curl --fail --location --silent --show-error \
  "https://github.com/jqlang/jq/releases/download/jq-$JQ_VERSION/jq-linux-amd64" \
  --output "$tool_tmp/jq-linux-amd64"
printf '%s  %s\n' "$JQ_SHA256" "$tool_tmp/jq-linux-amd64" | sha256sum --check --status
sudo install -m 0755 "$tool_tmp/jq-linux-amd64" /usr/local/bin/jq

curl --fail --location --silent --show-error \
  "https://github.com/oasdiff/oasdiff/releases/download/v$OASDIFF_VERSION/oasdiff_${OASDIFF_VERSION}_linux_amd64.tar.gz" \
  --output "$tool_tmp/oasdiff.tar.gz"
printf '%s  %s\n' "$OASDIFF_SHA256" "$tool_tmp/oasdiff.tar.gz" | sha256sum --check --status
tar -xzf "$tool_tmp/oasdiff.tar.gz" -C "$tool_tmp" oasdiff
sudo install -m 0755 "$tool_tmp/oasdiff" /usr/local/bin/oasdiff

sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends libxml2-utils
# apt cannot hold an exact build, so the pin is the runner image: ubuntu-24.04
# ships libxml2 2.9.14 for its whole life. A different image fails here rather
# than validating with an untested libxml.
xmllint --version 2>&1 | grep -q 'using libxml version 20914' || {
  xmllint --version >&2
  printf '%s\n' 'xmllint is not libxml 2.9.14; the CI pin is ubuntu-24.04' >&2
  exit 1
}
