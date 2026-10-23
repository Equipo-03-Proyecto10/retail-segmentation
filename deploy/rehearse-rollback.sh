#!/usr/bin/env bash
# Rehearse one service's rollback on the instance. F18-03 (issue #456).
#
#   sudo bash deploy/rehearse-rollback.sh <name>
#
# Builds, in a scratch directory, a release of the image tag <name> already
# serves whose compose file publishes it on another loopback port, so its
# /health/ready on the registered port cannot pass. deploy-service.sh deploys
# it as `<tag>+rehearsal`, and must fail at step 5 and roll back. The script
# then checks, and prints as Markdown for docs/evidence/f18-03-rollback.md:
#   - the release and image tag serving before serve again, and are ready;
#   - every other project's containers are the same containers, started at
#     the same moment (`docker ps --format '{{.Names}} {{.RunningFor}}'`);
#   - mosaiq.service, the web, is active and https://127.0.0.1/ answers 200.
# It exits 0 when all of that holds and 1 otherwise. The rehearsal's release
# directory is removed afterwards. It reads deploy/ and contracts/registry.yaml
# from the tree it lives in, and the service's files from its serving release.
#
# Environment: SERVICES_ROOT and everything deploy-service.sh reads.
set -uo pipefail

[[ $# -eq 1 && $1 =~ ^[a-z][a-z0-9-]*$ ]] || { echo "usage: rehearse-rollback.sh <name>" >&2; exit 64; }
name=$1
tree=$(cd "$(dirname "$0")/.." && pwd)
SERVICES_ROOT=${SERVICES_ROOT:-/opt/mosaiq/services}
home=$SERVICES_ROOT/$name
[[ -L $home/current ]] || { echo "rehearse-rollback: $name has no current release" >&2; exit 2; }
serving=$(readlink -f "$home/current")
link=$(readlink "$home/current")
tag=$(sed -n 's/^IMAGE_TAG=//p' "$serving/release.env")
port=$(awk -v service="$name" '
  $1 == "-" && $2 == "name:" { selected = ($3 == service) }
  selected && $1 == "port:" { print $2; exit }
' "$tree/contracts/registry.yaml")
[[ -n $tag && $port =~ ^[0-9]+$ ]] || { echo "rehearse-rollback: no tag or port for $name" >&2; exit 2; }
version="$tag+rehearsal"

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/contracts" "$scratch/services/$name"
cp -R "$tree/deploy" "$scratch/"
cp "$tree/contracts/registry.yaml" "$scratch/contracts/"
for part in compose.yaml nginx redis mongo; do
  [[ -e $serving/$part ]] && cp -R "$serving/$part" "$scratch/services/$name/"
done
printf '%s\n' "$tag" >"$scratch/services/$name/VERSION"
sed -i "s/127\.0\.0\.1:$port:/127.0.0.1:$((port + 10000)):/" "$scratch/services/$name/compose.yaml"
grep -q "127\.0\.0\.1:$((port + 10000)):" "$scratch/services/$name/compose.yaml" \
  || { echo "rehearse-rollback: could not move $name off port $port" >&2; exit 2; }

project=mosaiq-$name
# Every container of every other project, as id, start time and name.
others() {
  docker ps -q | xargs -r docker inspect -f \
    '{{index .Config.Labels "com.docker.compose.project"}} {{.Id}} {{.State.StartedAt}} {{.Name}}' \
    | awk -v p="$project" '$1 != p' | sort
}
image() {
  docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Image}}' | head -1
}

before_others=$(others)
before_image=$(image)
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
output=$(bash "$scratch/deploy/deploy-service.sh" "$name" "$version" 2>&1)
status=$?
after_others=$(others)
after_image=$(image)
after_link=$(readlink "$home/current")
ready=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$port/health/ready")
web=$(systemctl is-active mosaiq)
web_http=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 https://127.0.0.1/)
rm -rf "$home/releases/$version"

checks=()
failed=0
# check <what it shows> <command that holds when it does>
check() {
  local what=$1
  shift
  if "$@"; then checks+=("- [x] $what"); else checks+=("- [ ] $what"); failed=1; fi
}
# shellcheck disable=SC2329  # called through check
same() { [[ -n $1 && $1 == "$2" ]]; }
check "deploy-service.sh exited $status: 70 is a completed rollback" test "$status" -eq 70
check "it failed at step 5, readiness" grep -q "FAILED at step 5" <<<"$output"
check "current is still \`$link\`: \`$after_link\`" same "$link" "$after_link"
check "the image serving before, \`$before_image\`, serves again: \`$after_image\`" \
  same "$before_image" "$after_image"
check "\`/health/ready\` on $port answers $ready" grep -qx '2[0-9][0-9]' <<<"$ready"
check "every other project's $(wc -l <<<"$before_others") containers are the same containers, started at the same moment" \
  same "$before_others" "$after_others"
check "mosaiq.service is $web, and https://127.0.0.1/ answers $web_http" same "$web:$web_http" "active:200"

cat <<EOF
### $name, image tag $tag

Rehearsed at $started with \`$version\`, published on 127.0.0.1:$((port + 10000)) instead of $port.

$(printf '%s\n' "${checks[@]}")

<details><summary>deploy-service.sh output</summary>

\`\`\`text
$output
\`\`\`

</details>

\`docker ps --format '{{.Names}} {{.RunningFor}}'\` after the rehearsal:

\`\`\`text
$(docker ps --format '{{.Names}} {{.RunningFor}}' | sort)
\`\`\`

EOF
exit "$failed"
