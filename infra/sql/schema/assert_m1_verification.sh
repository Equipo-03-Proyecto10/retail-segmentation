#!/usr/bin/env bash
#
# Run verify_m1_schema.sql and assert that all 16 checks behaved as their
# `expected:` lines state.
#
# Why this exists. Nine of the sixteen checks pass by *raising* an error, so
# psql's exit status says nothing about whether the schema is correct: a
# database that had lost every constraint in the file would still exit 0 with
# `\set ON_ERROR_STOP off`, and one that had lost none would exit 0 as well.
# Reading the output is the acceptance criterion, so the assertion has to be on
# the output.
#
# The assertion is deliberately two-sided. The expected error count is exact,
# not a minimum: a check that should fail but succeeds drops the count, and a
# check that should succeed but fails raises it. Either direction fails the run.
#
# Usage, against a database already at `alembic upgrade head`:
#
#   DATABASE_MIGRATION_URL=postgresql+psycopg://postgres:postgres@localhost:5432/retail \
#     infra/sql/schema/assert_m1_verification.sh
#
# The database must be freshly migrated and otherwise empty. The SQL inserts its
# own fixtures and does not clean up after itself, so this is not idempotent —
# run it against a throwaway database, which is what CI does and what the
# Definition of Done asks a developer to do locally.
#
# The full psql output is printed before the assertions run, so the log of a
# passing run is itself the evidence the Sprint 0 Definition of Done requires to
# be attached to the S0-04a issue.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
verify_sql="${script_dir}/verify_m1_schema.sql"

if [[ -z "${DATABASE_MIGRATION_URL:-}" ]]; then
    echo "DATABASE_MIGRATION_URL is not set." >&2
    exit 2
fi

if ! command -v psql >/dev/null 2>&1; then
    echo "psql is not on PATH. Install postgresql-client, or run this through" >&2
    echo "the image that already has it:" >&2
    echo >&2
    echo "  docker run --rm --network host -v \"\$PWD\":/repo:ro \\" >&2
    echo "    -e DATABASE_MIGRATION_URL \\" >&2
    echo "    postgres:16 bash /repo/infra/sql/schema/assert_m1_verification.sh" >&2
    exit 2
fi

# SQLAlchemy carries the driver in the scheme; libpq does not understand it.
# Deriving the psql URL here keeps one connection string in .env.example rather
# than adding a second that can drift from the first.
psql_url="${DATABASE_MIGRATION_URL/+psycopg/}"

# psql runs the file with `\set ON_ERROR_STOP off`, so it exits 0 whether the
# checks behaved or not. A non-zero status here therefore means psql itself
# failed — it could not connect, or the file is missing — which is worth
# reporting differently from a schema that no longer satisfies S0-04a.
set +e
output="$(psql "${psql_url}" -f "${verify_sql}" 2>&1)"
psql_status=$?
set -e

echo "${output}"

if [[ "${psql_status}" -ne 0 ]]; then
    echo >&2
    echo "psql exited ${psql_status} before the checks could be assessed." >&2
    exit "${psql_status}"
fi

echo
echo "--------------------------------------------------------------------------"

failures=0

# Nine checks pass by raising. CHECK 9 raises twice, once for UPDATE and once
# for DELETE, so sixteen checks produce exactly ten error lines.
expected_errors=10
actual_errors="$(grep -c 'ERROR:' <<<"${output}" || true)"
if [[ "${actual_errors}" -ne "${expected_errors}" ]]; then
    echo "FAIL: expected exactly ${expected_errors} ERROR lines, found ${actual_errors}." >&2
    echo "      Too few means a constraint that should reject stopped rejecting." >&2
    echo "      Too many means a check that should succeed is now failing." >&2
    failures=$((failures + 1))
fi

# CHECK 6 reports PASS at two dates, CHECK 16 reports PASS once.
expected_pass=3
actual_pass="$(grep -c 'PASS' <<<"${output}" || true)"
if [[ "${actual_pass}" -ne "${expected_pass}" ]]; then
    echo "FAIL: expected exactly ${expected_pass} PASS rows, found ${actual_pass}." >&2
    failures=$((failures + 1))
fi

# Each entry is `CHECK number: regular expression the output must contain`.
# Naming the constraint rather than matching "ERROR" keeps a check from passing
# on somebody else's failure.
assertions=(
    "1|INSERT 0 3"
    "2|exclusion constraint \"ex_customer_segment_assignment_no_overlap\""
    "3|Key \\(customer_id, tstzrange\\(valid_from, valid_to\\)\\)=\\(1, \\[\"2025-09-01"
    "4|INSERT 0 3"
    "5|^COMMIT$"
    "6|as of 2025-10-01 \\|[[:space:]]*3 \\| PASS"
    "7|champions[[:space:]]*\\|[[:space:]]*at_risk[[:space:]]*\\|[[:space:]]*downgrade[[:space:]]*\\|[[:space:]]*3"
    "8|1 \\|[[:space:]]*-10 \\|[[:space:]]*-8200.00 \\| t[[:space:]]*\\| t"
    "9|append-only; UPDATE rejected"
    "9|append-only; DELETE rejected"
    "10|check constraint \"ck_sales_transaction_sign\""
    "11|channel in_store requires store_id"
    "12|unique constraint \"ux_sales_transaction_external\""
    "13|exclusion constraint \"ex_consent_record_no_overlap\""
    "14|is store-scoped and requires scope_store_id"
    "15|is global and must not carry scope_store_id"
    "16|3.000 \\|[[:space:]]*22.50 \\|[[:space:]]*5.00 \\|[[:space:]]*62.50 \\| PASS"
)

for assertion in "${assertions[@]}"; do
    check="${assertion%%|*}"
    pattern="${assertion#*|}"
    if ! grep -Eq "${pattern}" <<<"${output}"; then
        echo "FAIL: CHECK ${check} did not behave as its expected: line states." >&2
        echo "      Looked for: ${pattern}" >&2
        failures=$((failures + 1))
    fi
done

if [[ "${failures}" -gt 0 ]]; then
    echo >&2
    echo "${failures} assertion(s) failed. The M1 schema no longer satisfies S0-04a." >&2
    echo "Read docs/data/postgresql-model.md §3 before changing anything." >&2
    exit 1
fi

echo "All 16 checks behaved as their expected: lines state."
