#!/usr/bin/env bash
# Run the conformance suite against one image: stack.sh up, pytest, down
# (F13-09). Run from the repository root, with CONFORMANCE_CONTRACT_DIR set
# when the contract is not contracts/<name>/.
#
#   tests/conformance/run.sh <image> <service-dir> <port> [<expected-failure>]
#
# With <expected-failure>, the name of one case, the run passes only when the
# suite fails and that case is among its failures: CI's proof that the suite
# is not vacuous. The JUnit report goes to test-results/conformance-<name>.xml.
set -euo pipefail

[[ $# -eq 3 || $# -eq 4 ]] || {
  echo "usage: $0 <image> <service-dir> <port> [<expected-failure>]" >&2
  exit 2
}
name=$(basename "${2%/}")
expected=${4:-}
# A fresh directory per run: the previous one may keep PostgreSQL's socket.
CONFORMANCE_WORK=$(mktemp -d "${RUNNER_TEMP:-/tmp}/conformance-$name.XXXXXX")
rmdir "$CONFORMANCE_WORK"
export CONFORMANCE_WORK CONFORMANCE_ENV=$CONFORMANCE_WORK.env
report=test-results/conformance-$name${expected:+-negative}.xml
mkdir -p test-results

trap 'bash tests/conformance/stack.sh down' EXIT
bash tests/conformance/stack.sh up "$1" "$2" "$3"
set -a
# shellcheck source=/dev/null
. "$CONFORMANCE_ENV"
set +a

status=0
python3 -m pytest tests/conformance -rs -p no:cacheprovider --junitxml="$report" || status=$?

if [[ -z $expected ]]; then
  ((status == 0)) || bash tests/conformance/stack.sh logs
  exit "$status"
fi
if ((status == 0)); then
  echo "conformance: the suite passed against $1; $expected should have failed" >&2
  exit 1
fi
failed=$(python3 - "$report" <<'PY'
import sys
import xml.etree.ElementTree as ET

for case in ET.parse(sys.argv[1]).iter("testcase"):
    if case.find("failure") is not None or case.find("error") is not None:
        print(case.get("name"))
PY
)
if grep -qxF "$expected" <<<"$failed"; then
  echo "conformance: $expected failed against $1, as it must"
  exit 0
fi
echo "conformance: $expected did not fail against $1; failures were:" >&2
printf '%s\n' "$failed" >&2
exit 1
