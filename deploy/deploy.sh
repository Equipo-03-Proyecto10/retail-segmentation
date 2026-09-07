#!/usr/bin/env bash
# MOSAIQ — put a reviewed commit onto the instance. F6-06 (#90).
#
# Invoked over SSH by .github/workflows/deploy.yml after a merge to main, and
# safe to run by hand for a rollback or a recovery:
#
#   sudo bash deploy.sh <commit-sha>            deploy that commit
#   sudo bash deploy.sh <commit-sha> --dry-run  report, change nothing
#
# Run it from OUTSIDE the checkout — /tmp is what the workflow uses. bash reads
# a script incrementally, so executing this file from /opt/mosaiq/current while
# the checkout below rewrites it is a real way to corrupt a deploy.
#
# It does not touch the database. Schema changes stay a deliberate, manual step
# (AGENTS.md: the three scripts must run clean from empty; they are not
# migrations and re-running them against a live database is destructive).

set -euo pipefail

REPO_DIR=/opt/mosaiq/current
VENV=/opt/mosaiq/venv
APP_USER=mosaiq
SERVICE=mosaiq
# Overridable so the rollback path can be exercised deliberately — point the
# check at something that will not answer and the deploy must put the previous
# commit back. That test is the evidence for the third acceptance criterion.
HEALTH_URL=${HEALTH_URL:-https://127.0.0.1/}
HEALTH_ATTEMPTS=${HEALTH_ATTEMPTS:-10}

target=${1:-}
dry_run=${2:-}
if [[ -z $target ]]; then
    echo "usage: deploy.sh <commit-sha> [--dry-run]" >&2
    exit 64
fi

as_app() { sudo -u "$APP_USER" "$@"; }

say() { printf '\n=== %s\n' "$*"; }

# 200 through NGINX means TLS, the proxy, gunicorn and the database session all
# came back, which is more than `systemctl is-active` can tell us.
health_check() {
    local code=""
    for attempt in $(seq 1 $HEALTH_ATTEMPTS); do
        code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" || true)
        if [[ $code == 200 ]]; then
            echo "healthy after ${attempt} attempt(s): HTTP $code"
            return 0
        fi
        sleep 2
    done
    echo "unhealthy after ${HEALTH_ATTEMPTS} attempts: HTTP ${code:-no response}" >&2
    return 1
}

say "Deploy requested: $target"
as_app git -C "$REPO_DIR" fetch --quiet --prune origin

if ! as_app git -C "$REPO_DIR" cat-file -e "${target}^{commit}" 2>/dev/null; then
    echo "commit $target is not in the repository after fetch" >&2
    exit 65
fi

previous=$(as_app git -C "$REPO_DIR" rev-parse HEAD)
echo "currently serving: $previous"
echo "deploying:         $(as_app git -C "$REPO_DIR" rev-parse "$target")"

if [[ $dry_run == --dry-run ]]; then
    say "Dry run — nothing was changed"
    as_app git -C "$REPO_DIR" --no-pager log --oneline "${previous}..${target}" 2>/dev/null \
        | head -20 || true
    health_check
    exit 0
fi

if [[ $previous == "$(as_app git -C "$REPO_DIR" rev-parse "$target")" ]]; then
    say "Already serving that commit — verifying health and stopping"
    health_check
    exit 0
fi

# From here on, any failure puts the previous commit back. The instance runs one
# copy of the application, so this is a restart-and-verify deploy rather than a
# blue/green one: the guarantee is that a broken deploy is not LEFT serving, not
# that the switch is seamless. ADR-0011 says so plainly.
rollback() {
    trap - ERR
    say "FAILED — rolling back to $previous"
    as_app git -C "$REPO_DIR" checkout --quiet --force "$previous"
    as_app "$VENV/bin/pip" install --quiet --requirement "$REPO_DIR/web/requirements.txt"
    systemctl restart "$SERVICE"
    if health_check; then
        echo "rolled back; the previous version is serving again"
    else
        echo "ROLLBACK ALSO FAILED — the instance needs a person" >&2
        systemctl --no-pager --lines=30 status "$SERVICE" >&2 || true
    fi
    exit 70
}
trap rollback ERR

say "Checking out $target"
as_app git -C "$REPO_DIR" checkout --quiet --force "$target"

say "Installing dependencies"
as_app "$VENV/bin/pip" install --quiet --requirement "$REPO_DIR/web/requirements.txt"

say "Restarting $SERVICE"
systemctl restart "$SERVICE"

say "Health check"
health_check

trap - ERR
say "Deployed $(as_app git -C "$REPO_DIR" rev-parse --short HEAD)"
systemctl --no-pager --lines=0 status "$SERVICE" | head -3
