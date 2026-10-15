# Auth Redis liveness contract v1

auth-service produces the only session state that other service boundaries may
read directly.

## Published key

| Property | Contract |
|---|---|
| Redis instance | `redis-state` |
| Key | `pub:auth:v1:live:{sid}` where `{sid}` is the token's exact `sid` claim |
| Type | Redis STRING |
| Value | Opaque; consumers never read it. |
| Lifetime | TTL equals the session's remaining absolute lifetime, at most 12 hours from sign-in. |
| Creation | Set at sign-in and never extended, including during refresh. |
| Meaning | `EXISTS` returning `1` means the session is live; `0` means it is revoked. |

The consumer's only Redis access to auth state is the ACL selector
`%R~pub:auth:v1:live:* +exists`. A consumer checks `EXISTS` for the exact `sid`
with a 200 ms timeout. A missing key is 401 `session_revoked`. An unreachable or
slow `redis-state` is 503 `auth_unavailable`; authentication never falls back to
signature verification alone.

## Public signing keys

Every consuming service reads the mounted public key file
`/run/mosaiq/jwks.json`; services do not fetch keys from auth-service over HTTP.
The verifier reloads this file on `SIGHUP`, or after seeing an unknown `kid`, at
most once every 30 seconds.

## Compatibility

Changing the key's type, meaning, instance, or family is breaking. A breaking
change publishes a new `pub:auth:v2:live:*` family beside v1 until consumers
move.
