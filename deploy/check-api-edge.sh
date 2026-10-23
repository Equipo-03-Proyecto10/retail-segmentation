#!/usr/bin/env bash
# Outside-in checks for the Cloudflare edge of F18-02 (issue #452). Run from
# any workstation checkout, outside the instance:
#
#   bash deploy/check-api-edge.sh
#
# Prints nothing and exits 0 when every check holds; one `FAIL:` line per
# problem and exit 1 otherwise. Set VERBOSE=1 to print each successful
# observation as an `ok:` line. No request follows redirects.
#
# Check 3 triggers the sign-in limit for the caller's own address for about a
# minute (rate 6r/m, burst 5). Run it when nobody needs to sign in from that
# address.
#
# The origin-port check expects the GCP firewall to drop these ports; the
# containers also bind to 127.0.0.1 only. It is an outside reachability check,
# not a replacement for the instance-side check.
#
# Environment:
#   BASE_URL  published URL (default: https://mosaiq.maxthecoder.online)
#   ORIGIN_IP origin address for port probes (default: the address in
#             docs/infra.md; otherwise required)
#   REGISTRY  registry path relative to the repository root (default:
#             contracts/registry.yaml)
#   VERBOSE   set to 1 to print successful observations (default: 0)
set -uo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BASE_URL=${BASE_URL:-https://mosaiq.maxthecoder.online}
BASE_URL=${BASE_URL%/}
REGISTRY=${REGISTRY:-contracts/registry.yaml}
[[ $REGISTRY == /* ]] || REGISTRY=$root/$REGISTRY
VERBOSE=${VERBOSE:-0}

failed=0
fail() {
  echo "FAIL: $*"
  failed=1
}

ok() {
  [[ $VERBOSE == 1 ]] && echo "ok: $*"
}

tmpdir=$(mktemp -d)
trap 'rm -rf -- "$tmpdir"' EXIT
headers=$tmpdir/headers
body=$tmpdir/body
large_body=$tmpdir/large-body
signin_body=$tmpdir/signin-body
refresh_body=$tmpdir/refresh-body

# Keep curl's response body and headers private to the check. In particular,
# this prevents a generated credential or token from appearing in output.
curl_request() {
  local response_headers=$1 response_body=$2 url=$3
  shift 3
  : >"$response_headers"
  : >"$response_body"
  curl --silent --show-error --connect-timeout 5 --max-time 20 \
    -D "$response_headers" -o "$response_body" -w '%{http_code}' \
    "$@" "$url" 2>/dev/null || true
}

has_header() {
  local file=$1 name=$2
  grep -Eiq "^${name}[[:space:]]*:" "$file"
}

has_content_type() {
  local file=$1 type=$2
  grep -Eiq "^content-type[[:space:]]*:[[:space:]]*${type}([[:space:]]*;|[[:space:]]*$)" \
    "$file"
}

cf_cache_bad_values() {
  tr -d '\r' <"$1" | awk '
    tolower($1) == "cf-cache-status:" {
      value = $0
      sub(/^[^:]*:[[:space:]]*/, "", value)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (tolower(value) != "dynamic") print value
    }
  '
}

random_hex() {
  od -An -N "$1" -tx1 /dev/urandom | tr -d ' \n'
}

services=()
registry_ports=()
if [[ -r $REGISTRY ]]; then
  while IFS=$'\t' read -r name port; do
    [[ -n ${name:-} && -n ${port:-} ]] || continue
    if [[ $name =~ ^[a-z][a-z0-9-]*$ && $port =~ ^[0-9]+$ ]]; then
      services+=("$name")
      registry_ports+=("$port")
    else
      fail "invalid registry entry: ${name:-?} ${port:-?}"
    fi
  done < <(
    awk '
      /^  - name:[[:space:]]*/ { name = $3; next }
      /^    port:[[:space:]]*/ {
        if (name != "") {
          print name "\t" $2
          name = ""
        }
      }
    ' "$REGISTRY"
  )
  ((${#services[@]} > 0)) || fail "registry has no service name/port entries: $REGISTRY"
else
  fail "registry is not readable: $REGISTRY"
fi

# Find the sole configuration whose sign-in limit zone has the required shape.
# The service name is taken from that file's path, never written here.
signin_candidates=()
while IFS= read -r -d '' conf; do
  conf_name=$(basename "$conf" .conf)
  zone_name=$(printf '%s' "$conf_name" | tr '-' '_')
  if grep -qF "limit_req zone=mosaiq_${zone_name}_signin" "$conf"; then
    signin_candidates+=("$(basename "$conf" .conf)")
  fi
done < <(find "$root/services" -type f -path '*/nginx/*.conf' -print0)

signin_name=
if ((${#signin_candidates[@]} == 1)); then
  signin_name=${signin_candidates[0]}
else
  fail "expected one sign-in zone configuration, found ${#signin_candidates[@]}"
fi

signin_registered=0
for entry in "${services[@]}"; do
  [[ $entry == "$signin_name" ]] && signin_registered=1
done
[[ $signin_registered -eq 1 || -z $signin_name ]] || \
  fail "sign-in zone configuration is not present in the registry"

# The configured address is the documented origin unless the caller overrides
# it. Restrict the default parser to the published A-record table.
origin_from_docs=
if [[ -r $root/docs/infra.md ]]; then
  origin_from_docs=$(awk '
    /^### DNS and the published certificate$/ { section = 1; next }
    section && /^## / { exit }
    section && /^\| Record \|/ {
      if (match($0, /[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*/)) {
        print substr($0, RSTART, RLENGTH)
        exit
      }
    }
  ' "$root/docs/infra.md")
fi

if [[ -n ${ORIGIN_IP:-} ]]; then
  origin_ip=$ORIGIN_IP
elif [[ -n $origin_from_docs ]]; then
  origin_ip=$origin_from_docs
else
  origin_ip=
  fail "ORIGIN_IP is not set and docs/infra.md has no documented origin address"
fi

if [[ -n $origin_ip && ! $origin_ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
  fail "ORIGIN_IP is not an IPv4 address"
  origin_ip=
fi

# 1. Readiness, HSTS and Cloudflare cache behavior.
for name in "${services[@]}"; do
  url="$BASE_URL/api/$name/health/ready"
  status=$(curl_request "$headers" "$body" "$url" -X GET)
  if [[ ! $status =~ ^2[0-9][0-9]$ ]]; then
    fail "readiness for $name returned HTTP ${status:-unknown}, not 2xx"
  else
    hsts_ok=1
    if ! has_header "$headers" strict-transport-security; then
      fail "readiness for $name has no Strict-Transport-Security header"
      hsts_ok=0
    fi
    cache_bad=$(cf_cache_bad_values "$headers")
    cache_ok=1
    if [[ -n $cache_bad ]]; then
      fail "readiness for $name has non-DYNAMIC cf-cache-status"
      cache_ok=0
    fi
    if [[ $hsts_ok -eq 1 && $cache_ok -eq 1 ]]; then
      ok "readiness $name returned $status with HSTS and no cached response"
    fi
  fi
done

# Make one 65 KiB body for every gateway refusal probe.
head -c 66560 /dev/zero | tr '\0' a >"$large_body"
large_size=$(wc -c <"$large_body")
[[ $large_size -eq 66560 ]] || fail "could not create the 66560-byte request body"

# 2. A body over 64 KiB is refused by the edge in both response formats.
for name in "${services[@]}"; do
  url="$BASE_URL/api/$name/v1/"

  status=$(curl_request "$headers" "$body" "$url" -X POST \
    -H 'Accept: application/json' -H 'Content-Type: application/json' \
    --data-binary "@$large_body")
  [[ $status == 413 ]] || fail "oversize JSON request for $name returned HTTP ${status:-unknown}, not 413"
  has_content_type "$headers" application/json || \
    fail "oversize JSON response for $name is not application/json"
  grep -Eiq '"code"[[:space:]]*:[[:space:]]*"payload_too_large"' "$body" || \
    fail "oversize JSON response for $name has no payload_too_large code"

  status=$(curl_request "$headers" "$body" "$url" -X POST \
    -H 'Accept: application/xml' -H 'Content-Type: application/json' \
    --data-binary "@$large_body")
  [[ $status == 413 ]] || fail "oversize XML request for $name returned HTTP ${status:-unknown}, not 413"
  has_content_type "$headers" application/xml || \
    fail "oversize XML response for $name is not application/xml"
  grep -Eiq '<code>[[:space:]]*payload_too_large[[:space:]]*</code>' "$body" || \
    fail "oversize XML response for $name has no payload_too_large code"
  if [[ $status == 413 ]] && has_content_type "$headers" application/xml && \
    grep -Eiq '<code>[[:space:]]*payload_too_large[[:space:]]*</code>' "$body"; then
    ok "oversize body for $name returned the 413 JSON and XML envelopes"
  fi
done

# 3 and 4. The generated credentials and refresh token are deliberately bogus.
if [[ -n $signin_name && $signin_registered -eq 1 ]]; then
  signin_email="edge-probe-$(random_hex 12)@example.invalid"
  signin_password=$(random_hex 24)
  # A well-formed sign-in, so the signer answers a failed sign-in (401), not
  # an invalid body (422): device_label is required by the contract.
  printf '{"email":"%s","password":"%s","device_label":"edge check"}\n' \
    "$signin_email" "$signin_password" >"$signin_body"

  first_429=0
  sent=0
  before_429=()
  for n in $(seq 1 15); do
    spoofed_ip="198.51.100.$n"
    status=$(curl_request "$headers" "$body" "$BASE_URL/api/$signin_name/v1/sessions" -X POST \
      -H 'Accept: application/json' -H 'Content-Type: application/json' \
      -H "X-Forwarded-For: $spoofed_ip" -H "X-Real-IP: $spoofed_ip" \
      --data-binary "@$signin_body")
    sent=$n
    if [[ $status == 429 ]]; then
      first_429=$n
      has_content_type "$headers" application/json || \
        fail "sign-in rate-limit response is not application/json"
      grep -Eiq '"code"[[:space:]]*:[[:space:]]*"rate_limited"' "$body" || \
        fail "sign-in HTTP 429 response has no rate_limited code"
      break
    fi
    [[ $status != 000 ]] || fail "sign-in request $n did not receive an HTTP response"
    before_429+=("$status")
  done

  if [[ $first_429 -eq 0 ]]; then
    fail "sign-in rate limit did not return 429 after $sent requests"
  else
    ok "sign-in rate limit returned 429 at request $first_429, after ${before_429[*]:-none}"
  fi

  refresh_429=0
  refresh_no_response=0
  for n in $(seq 1 10); do
    refresh_token=$(random_hex 24)
    printf '{"refresh_token":"%s"}\n' "$refresh_token" >"$refresh_body"
    status=$(curl_request "$headers" "$body" "$BASE_URL/api/$signin_name/v1/sessions/refresh" -X POST \
      -H 'Accept: application/json' -H 'Content-Type: application/json' \
      --data-binary "@$refresh_body")
    if [[ $status == 429 ]]; then
      refresh_429=$((refresh_429 + 1))
    elif [[ $status == 000 ]]; then
      refresh_no_response=$((refresh_no_response + 1))
    fi
  done
  [[ $refresh_429 -eq 0 ]] || \
    fail "refresh returned 429 for $refresh_429 of 10 requests"
  [[ $refresh_no_response -eq 0 ]] || \
    fail "refresh returned no HTTP response for $refresh_no_response of 10 requests"
  if [[ $refresh_429 -eq 0 && $refresh_no_response -eq 0 ]]; then
    ok "10 refresh requests were not rate limited"
  fi
fi

# 5. The server-rendered web remains reachable without following redirects.
web_paths=(/login / /reports/ /catalog/stock)
for path in "${web_paths[@]}"; do
  status=$(curl --silent --show-error --connect-timeout 5 --max-time 20 \
    -o /dev/null -w '%{http_code}' "$BASE_URL$path" 2>/dev/null) || status=000
  if [[ ! $status =~ ^(200|3[0-9][0-9])$ ]]; then
    fail "web path $path returned HTTP ${status:-unknown}, expected 200 or 3xx"
  else
    ok "web path $path returned $status"
  fi
done

# 6. The origin and infrastructure ports must be unreachable from outside.
ports=("${registry_ports[@]}" 6379 27017)
if [[ -n $origin_ip ]]; then
  for port in "${ports[@]}"; do
    if timeout 3 bash -c "exec 3<>/dev/tcp/\$1/\$2" _ "$origin_ip" "$port" \
      >/dev/null 2>&1; then
      fail "origin port $port is reachable on $origin_ip"
    else
      ok "origin port $port is unreachable"
    fi
  done
fi

exit "$failed"
