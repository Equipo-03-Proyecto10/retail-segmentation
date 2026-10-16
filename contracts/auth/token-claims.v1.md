# Auth token claims v1

auth-service is the only signer. Every consumer pins this contract while
enforcing its own operation-to-role policy.

## JOSE header

| Member | Contract |
|---|---|
| `alg` | Exactly `RS256`; `none` and symmetric algorithms are refused. |
| `kid` | Non-empty key identifier used to select a public key from the mounted JWK Set. |

## Claims

| Claim | Contract |
|---|---|
| `iss` | The exact configured auth-service issuer; consumers compare it exactly. |
| `aud` | Exactly `mosaiq-api`. |
| `sub` | The authenticated `app_user.user_id`, a UUID string. |
| `sid` | The UUID of the session whose liveness key must exist. |
| `role` | One immutable role code: `ADMIN`, `ANALYST`, `STORE_MANAGER`, `MARKETING`, `INVENTORY_PLANNER`, `AUDITOR`, or `CUSTOMER`. |
| `jti` | A unique identifier for this access token. |
| `iat` | NumericDate at which this access token was issued. |
| `exp` | NumericDate after which this access token is refused. |

There is no permissions claim and there is no name or email claim. Consumers
must not derive either identity data or authority beyond the local policy for
`role`.

An access token lasts 10 minutes. Its `exp` never passes the session's absolute
end, which is 12 hours from sign-in; a token issued near that end therefore has
a shorter lifetime.

## Compatibility

Adding a claim is non-breaking because v1 consumers ignore claims they do not
use. Removing or renaming any claim above is breaking and requires a v2 claims
contract.
