# Application logging

The `web` logger owns one stderr handler. Every `web.*` module inherits its
`LOG_LEVEL`; services use `logging.getLogger(__name__)` without importing Flask.
Under systemd stderr reaches journald; under Compose it reaches container logs.
Repeated application creation replaces the handler instead of adding duplicates.

The handler adds the timestamp, level, module, request reference, actor ID and
client address. Outside an HTTP request the last three are `-`. Proxy addresses
follow the configured `TRUSTED_PROXY_HOPS`, as other request handling does.

| Event | Level | Fields |
|---|---|---|
| Application started | INFO | Environment, requested log level, proxy hops, upload directory and file/request limits |
| Sign-in succeeded or refused | INFO | Submitted email (at most 254 characters), plus request context |
| Unusable stored password hash | WARNING | Email, `reason=unusable_hash`; the visitor still gets the generic 401 |
| Sign-out | INFO | Request context captured before the session is cleared |
| Service write succeeded or refused | INFO | Operation; refusal exception class; success appears only after commit |
| Catalog constraint refusal | INFO | Entity, operation and exception class; no SQL or database error text |
| Segment run started/completed | INFO | Window; completed counts and elapsed seconds after commit |
| Upload refused | INFO | Validation reason |
| HTTP refusal | INFO | Status, method, path and reference shared with the error page |
| Unexpected HTTP failure | ERROR | Existing error-handler traceback and request reference |

Passwords, hashes, cookies, session tokens, connection URLs, secret keys, file
contents and raw form payloads are never added to event logs. Visitor-supplied
strings use `%r` so newline characters cannot forge another log entry. Emails
and actor IDs are operational data; access to the journal remains restricted to
the instance operators.

The PostgreSQL audit log remains the history of row changes. These application
events explain attempts, refusals and startup state that never become audit rows.

Use `journalctl -u mosaiq` and search for the reference shown on an error page,
or `docker compose logs web` for the local execution path.
