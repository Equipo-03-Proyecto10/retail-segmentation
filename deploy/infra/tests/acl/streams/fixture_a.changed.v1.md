# `stream:fixture_a.v1.changed`

A test fixture for `deploy/infra/check-redis.sh`, not a real stream. The
producer is `svc_fixture_a`; `svc_fixture_b` consumes it. The check reads only
the file name: `<producer>.<event>.v1.md`.
