#!/usr/bin/env bash
# The instance checks of ADR-0040 (F18-01, issue #448). Run as root on the
# instance, from anywhere:
#
#   sudo bash /opt/mosaiq/current/deploy/check-services.sh
#
# Prints nothing and exits 0 when every check holds; prints one line per
# problem and exits 1 otherwise. It changes nothing, apart from one throwaway
# container (check 6) that is removed when it exits.
#
#   1. The only non-loopback listeners are sshd :22 and NGINX :80 and :443.
#   2. Every container has a mem_limit, none was OOM-killed, and every image is
#      ghcr.io/equipo-03-proyecto10/mosaiq-*, redis: or mongo:.
#   3. mosaiq and mosaiq-infra are active, and each staged service in
#      /opt/mosaiq/services/<name> has an active mosaiq-svc@<name> and a running
#      project mosaiq-<name>.
#   4. Every service container runs as a non-root user with CapDrop ALL, and a
#      .pem is mounted into mosaiq-auth only.
#   5. pg_hba has a `local retail svc_<suffix> scram-sha-256` line for each
#      service role, above `local all all peer`, and none for
#      svc_products_bench; listen_addresses is localhost.
#   6. A container run with the service user, cap_drop ALL and the socket mount
#      cannot connect as postgres.
#
# This file is the one place under deploy/ allowed to name a service (the
# signer that holds the .pem), as ADR-0040's compliance check excludes it.
set -uo pipefail

[[ $(id -u) -eq 0 ]] || { echo "check-services: run as root" >&2; exit 2; }
SERVICES_DIR=${SERVICES_DIR:-/opt/mosaiq/services}
GHCR=ghcr.io/equipo-03-proyecto10/mosaiq-

# psql as postgres over the local socket. Every statement is fixed text: no value
# is ever interpolated into SQL.
sql() { runuser -u postgres -- psql -X -At -v ON_ERROR_STOP=1 -d retail -c "$1"; }

problems=$(
  # 1. Non-loopback listeners.
  ss -Hltnp | while read -r _state _recv _send local _peer process; do
    case $local in 127.*:* | '[::1]':*) continue ;; esac
    port=${local##*:}
    case "$port $process" in
      '22 '*'"sshd"'* | '80 '*'"nginx"'* | '443 '*'"nginx"'*) ;;
      *) echo "listener outside loopback: $local ${process:-unknown process}" ;;
    esac
  done

  # 2 and 4. Every container: limits, OOM, image origin; service containers:
  # user, capabilities and the .pem mount.
  docker ps -aq | while read -r id; do
    docker inspect -f '{{.Name}}|{{.HostConfig.Memory}}|{{.State.OOMKilled}}|{{.Config.Image}}|{{.Config.User}}|{{range .HostConfig.CapDrop}}{{.}} {{end}}|{{index .Config.Labels "com.docker.compose.project"}}|{{range .Mounts}}{{.Source}} {{end}}' "$id"
  done | while IFS='|' read -r name memory oom image user capdrop project mounts; do
    name=${name#/}
    [[ $memory -gt 0 ]] || echo "container without a mem_limit: $name"
    [[ $oom == false ]] || echo "container was OOM-killed: $name"
    case $image in
      "$GHCR"* | redis:* | mongo:*) ;;
      *) echo "container from an unexpected image: $name $image" ;;
    esac
    case $project in
      mosaiq-infra | '') ;;
      mosaiq-*)
        case $user in '' | 0 | 0:* | root | root:*) echo "service container runs as root: $name" ;; esac
        case " $capdrop" in *' ALL '*) ;; *) echo "service container does not drop ALL capabilities: $name" ;; esac
        case $mounts in *.pem*) [[ $project == mosaiq-auth ]] || echo "a .pem is mounted into $name ($project)" ;; esac
        ;;
    esac
  done

  # 3. Units and projects.
  running=$(docker ps --format '{{.Label "com.docker.compose.project"}}' | sort -u)
  for unit in mosaiq mosaiq-infra; do
    systemctl is-active --quiet "$unit" || echo "unit not active: $unit"
  done
  grep -qx mosaiq-infra <<<"$running" || echo "project not running: mosaiq-infra"
  for dir in "$SERVICES_DIR"/*/; do
    [[ -d $dir ]] || continue
    name=$(basename "$dir")
    systemctl is-active --quiet "mosaiq-svc@$name" || echo "unit not active: mosaiq-svc@$name"
    grep -qx "mosaiq-$name" <<<"$running" || echo "project not running: mosaiq-$name"
  done

  # 5. pg_hba and the listener address.
  peer=$(sql "SELECT min(line_number) FROM pg_hba_file_rules
              WHERE type = 'local' AND database = ARRAY['all'] AND user_name = ARRAY['all']
                AND auth_method = 'peer'")
  [[ -n $peer ]] || echo "pg_hba: no 'local all all peer' line"
  sql "SELECT line_number || ': ' || error FROM pg_hba_file_rules WHERE error IS NOT NULL" \
    | sed 's/^/pg_hba: invalid line /'
  # name|line of each single-role `local retail <role> scram-sha-256` rule.
  scram=$(sql "SELECT user_name[1] || '|' || line_number FROM pg_hba_file_rules
               WHERE type = 'local' AND database = ARRAY['retail'] AND auth_method = 'scram-sha-256'
                 AND cardinality(user_name) = 1")
  roles=$(sql "SELECT rolname FROM pg_roles
               WHERE rolname LIKE 'svc\_%' AND rolname <> 'svc_products_bench' ORDER BY rolname")
  [[ $(grep -c . <<<"$roles") -eq 15 ]] || echo "pg_roles: expected 15 service roles, found $(grep -c . <<<"$roles")"
  for role in $roles; do
    line=$(grep "^$role|" <<<"$scram" | head -1 | cut -d'|' -f2)
    if [[ -z $line ]]; then
      echo "pg_hba: no 'local retail $role scram-sha-256' line"
    elif [[ -n $peer && $line -gt $peer ]]; then
      echo "pg_hba: the line for $role ($line) is below 'local all all peer' ($peer)"
    fi
  done
  [[ $(sql "SELECT count(*) FROM pg_hba_file_rules WHERE 'svc_products_bench' = ANY (user_name)") -eq 0 ]] \
    || echo "pg_hba: a line names svc_products_bench"
  listen=$(sql "SHOW listen_addresses")
  [[ $listen == localhost ]] || echo "postgresql: listen_addresses is '$listen', not localhost"

  # 6. With the service user, no capabilities and the socket, postgres is out of
  # reach. The first service image that has Python runs the probe; exit 3 means
  # the connection was refused, as it must be.
  probe='import sys, psycopg
try:
    psycopg.connect("host=/run/postgresql dbname=retail user=postgres", connect_timeout=5).close()
except Exception:
    sys.exit(3)
sys.exit(0)'
  outcome=none
  for image in $(docker ps --format '{{.Image}}' | grep "^$GHCR" | sort -u); do
    docker run --rm --network none --user 10001:10001 --cap-drop ALL \
      --security-opt no-new-privileges:true --read-only --memory 64m \
      -v /run/postgresql:/run/postgresql:ro --entrypoint python "$image" -c "$probe" >/dev/null 2>&1
    case $? in
      3) outcome=refused; break ;;
      0) outcome=connected; break ;;
      *) ;;
    esac
  done
  case $outcome in
    refused) ;;
    connected) echo "a service-user container connected as postgres through /run/postgresql" ;;
    none) echo "no running service image with Python to probe the postgres socket" ;;
  esac
)

if [[ -n $problems ]]; then
  printf '%s\n' "$problems"
  exit 1
fi
