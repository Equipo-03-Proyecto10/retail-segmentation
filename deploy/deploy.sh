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

# This script does NOT install the NGINX config — that stays a manual step
# (deploy/README.md, F6-01 step 3). Nothing used to report when the two fell out
# of step, so a merged change to HSTS, the CSP, the real-IP trust list or
# server_name could sit in the repository while the instance served the previous
# file, with nothing visibly broken. It is reported here on every run. #169.
# The published documentation tree (deliverable 14). It is a copy rather than a
# symlink into the checkout: NGINX serves it directly, and pointing the alias at
# a git working tree makes every `git checkout` briefly visible to the public.
DOCS_DST=${DOCS_DST:-/opt/mosaiq/docs}

NGINX_CONF_DIR=${NGINX_CONF_DIR:-/etc/nginx/conf.d}
# Only the files that reach the instance: mosaiq.compose.conf is for local
# verification against compose and is never installed.
NGINX_CONFS=(mosaiq.conf cloudflare-real-ip.conf)

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

# Report where the live proxy config differs from the commit being served, and
# change nothing. Always returns 0: a proxy file this script is not allowed to
# touch is not a reason to roll back an application deploy that is healthy.
nginx_drift_check() {
    local name repo live
    local -a drifted=()

    if [[ ! -d $NGINX_CONF_DIR ]]; then
        echo "no $NGINX_CONF_DIR on this host — skipping the drift check"
        return 0
    fi

    for name in "${NGINX_CONFS[@]}"; do
        repo="$REPO_DIR/deploy/nginx/$name"
        live="$NGINX_CONF_DIR/$name"
        # A file the target commit does not carry yet is not drift.
        [[ -f $repo ]] || continue
        if [[ ! -f $live ]] || ! cmp -s "$repo" "$live"; then
            drifted+=("$name")
        fi
    done

    if [[ ${#drifted[@]} -eq 0 ]]; then
        echo "matches the deployed commit"
        return 0
    fi

    local rule="====================================================================="
    printf '\n'
    printf '!!! %s\n' "$rule" \
        "NGINX CONFIG DRIFT — the live proxy does not match this commit" "$rule"

    for name in "${drifted[@]}"; do
        repo="$REPO_DIR/deploy/nginx/$name"
        live="$NGINX_CONF_DIR/$name"
        if [[ ! -f $live ]]; then
            printf '!!! %s: in the repository, not installed\n' "$name"
            continue
        fi
        printf '!!! %s: the installed copy differs — installed vs repository:\n' "$name"
        diff -u "$live" "$repo" | head -40 | sed 's/^/!!!   /' || true
    done

    printf '!!!\n'
    printf '!!! %s\n' "Nothing above was changed. To apply it on the instance:"
    for name in "${drifted[@]}"; do
        printf '!!!   sudo cp %s %s\n' "$REPO_DIR/deploy/nginx/$name" "$NGINX_CONF_DIR/$name"
    done
    printf '!!!   %s\n' "sudo nginx -t && sudo systemctl reload nginx"
    printf '!!! %s\n' "Keeping this manual is deliberate — deploy/README.md, F6-06." "$rule"
    printf '\n'
    return 0
}

# Publish docs/ from the commit being deployed. Deliverables 10, 13 and 14 live
# in that tree, and until #182 this was a manual scp nobody ran: the published
# copy sat on an ADR-0006-era snapshot for weeks while every merge went green.
#
# Two failure modes are handled here because both actually happened. The tree
# has to arrive complete, and it has to be readable: SELinux is Enforcing, a
# copy carries the source's context, and httpd_t cannot read anything labelled
# otherwise. `restorecon` applies the fcontext rule that is already in policy.
#
# Like nginx_drift_check, this never rolls back. A documentation copy is not a
# reason to take a healthy application off the instance — but a failure here is
# printed loudly, because a green deploy that silently drops a graded
# deliverable is worse than a red one.
publish_docs() {
    local src="$REPO_DIR/docs"

    if [[ ! -d $src ]]; then
        echo "no docs/ in $target — nothing to publish"
        return 0
    fi

    if ! rm -rf "$DOCS_DST" || ! cp -a "$src" "$DOCS_DST"; then
        echo "!!! FAILED to copy $src to $DOCS_DST — the published documentation" >&2
        echo "!!! may be missing or incomplete. The application is unaffected." >&2
        return 0
    fi

    chown -R "$APP_USER:$APP_USER" "$DOCS_DST" || true

    if command -v restorecon >/dev/null 2>&1; then
        restorecon -R "$DOCS_DST" >/dev/null 2>&1 || true
    fi

    local files
    files=$(find "$DOCS_DST" -type f 2>/dev/null | wc -l)
    echo "published $files files to $DOCS_DST"

    # Labelled wrong means present and unreadable: NGINX answers 403, and a
    # Cloudflare HIT on an older copy can make a spot check look fine anyway.
    if command -v find >/dev/null 2>&1; then
        local unlabelled
        unlabelled=$(find "$DOCS_DST" -context '*user_tmp_t*' 2>/dev/null | wc -l)
        if [[ ${unlabelled:-0} -gt 0 ]]; then
            echo "!!! $unlabelled file(s) still labelled user_tmp_t — NGINX will answer 403." >&2
            echo "!!! Fix: sudo restorecon -Rv $DOCS_DST" >&2
        fi
    fi

    return 0
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
    say "NGINX config"
    nginx_drift_check
    say "Documentation"
    echo "would publish $REPO_DIR/docs to $DOCS_DST"
    exit 0
fi

if [[ $previous == "$(as_app git -C "$REPO_DIR" rev-parse "$target")" ]]; then
    say "Already serving that commit — verifying health and stopping"
    health_check
    say "Documentation"
    publish_docs
    say "NGINX config"
    nginx_drift_check
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

say "Documentation"
publish_docs

say "NGINX config"
nginx_drift_check
