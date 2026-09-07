# F5-02 — Negative tests (#75)

Captured locally on 2026-09-06 with Python 3.12.14 against `develop` at
`58e0282` plus this change. The real Flask routes, middleware, services,
queries and templates run with a mocked PostgreSQL connector. Uploads use a
temporary directory. This is application-test evidence, not deployment evidence.

| Cases in `tests/test_negative_flows.py` | Verified result |
|---|---|
| Every protected GET/POST, anonymous (42 cases) | GET redirects to `/login` with the original path; POST returns 403; no database connection |
| Every protected catalog, user, audit and segment route, CUSTOMER (41 cases) | 403, logged refusal, no database connection |
| All administration routes, the other five non-admin roles (155 cases) | 403 before any database connection |
| Route-inventory guard; forged session (2 cases) | Every protected endpoint/method is represented; forged cookie grants no access |
| Unknown email, wrong password, inactive account, empty login, SQL injection (5 cases) | Same generic 401; no authenticated session or writes; email remains a SQL parameter |
| Empty creation forms across six catalogs; malformed, excessive or negative product fields (12 cases) | 400 with a field error; no INSERT, UPDATE, DELETE or commit |
| Script in a rejected user form; injection/script search across six listings (7 cases) | Script is HTML-escaped; password is not echoed; search values remain bound parameters |
| HTML upload, empty image, oversized image (3 cases) | 400 with explanation; no product write or file |
| Duplicate product and missing category, simulated database refusals (2 cases) | 409 with explanation; rollback, no commit or database details in HTML |
| Missing URL, unsupported method, database outage (3 cases) | Branded 404/405/500; outage reference matches its log entry; no traceback in HTML |

Reproduce from a clean clone using the README's virtual environment and
`pip install -r web/requirements-dev.txt`, then run:

```bash
pytest tests/test_negative_flows.py -v --junitxml=test-results/negative-flows.xml
pytest --junitxml=test-results/pytest.xml
black --check .
ruff check .
```

Results: **272 negative-flow cases passed; 455 tests passed overall; format and lint passed.** CI captures every case, including failures, in the `pytest-results` artifact on the pull request's **CI** run. Generated local reports are ignored by Git.

Chromium rendered the actual 403 response and rejected user form with the shipped CSS at 375 px and 1440 px; neither overflowed horizontally. Screenshots: [403, 375 px](f5-02-forbidden-375.png), [403, 1440 px](f5-02-forbidden-1440.png), [invalid form, 375 px](f5-02-invalid-form-375.png), [invalid form, 1440 px](f5-02-invalid-form-1440.png).

No schema, seed, configuration or application behavior changes. PostgreSQL constraint enforcement is covered separately by [F2-07](f2-07-integrity-verification.md) and the existing CI database job. These mocks demonstrate query binding and refusal handling, not live-database execution. Upload checks cover declared MIME type and size, not image decoding. Broader input validation remains the open prerequisite [#71](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/71); independent acceptance verification and a reviewed merge are still required before #75 is Done.
