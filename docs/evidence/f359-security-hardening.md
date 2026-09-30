# #359 — security hardening evidence

The focused regression set covers all five findings in the issue:

```text
./.venv/bin/pytest -q tests/test_config.py tests/test_security.py \
  tests/test_server_side_sessions.py tests/test_nginx_security_headers.py \
  tests/test_admin_crud.py tests/test_email_case.py tests/test_write_services.py
164 passed
```

- `app_session.last_seen_at` is renewed only after both the 30-minute idle and
  8-hour absolute SQL age checks pass.
- `a@b` is refused by the application validator and the dotted-domain schema
  check.
- Lowercase, Unicode and `ADM1N`/`ADMLN` role-code look-alikes are refused;
  only role 1 may carry `ADMIN`.
- Flask responses and both NGINX configurations carry the security baseline,
  including `max-age=31536000; includeSubDomains`; NGINX suppresses Flask's
  upstream copies before adding the proxy-boundary values, avoiding duplicate
  CSP policies.

Repository-wide quality checks:

```text
./.venv/bin/black --check .  # 191 files unchanged
./.venv/bin/ruff check .    # All checks passed
./.venv/bin/pytest -q       # 2948 passed
```

The schema change is in `sql/01_schema.sql`; the normal database CI job runs
the three ordered scripts against an empty PostgreSQL instance.

This evidence verifies the repository change, not a deployment. The captured
production headers in `f6-07-proof-of-deployment.md` remain unchanged until the
new NGINX configuration is deployed and independently recaptured.
