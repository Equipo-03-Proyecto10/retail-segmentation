# Runbook — auth-service's signing key

The private key that signs every access token (ADR-0036), where it lives on
the instance, and how it rotates with no rebuild and no sign-out. F14-02
(#413). Rotation is a runbook, not automation: services reject tokens signed
with a key their JWK Set does not hold until they reload the file.

## Where things live

| Path on the instance | What | Mode and owner |
|---|---|---|
| `/etc/mosaiq/keys/auth-<kid>.pem` | The private key. Mounted read-only into auth alone, at `/run/mosaiq/auth-<kid>.pem` | `0400`, UID 10001 |
| `/etc/mosaiq/keys/jwks.json` | The JWK Set: public members only. Mounted read-only into every service, at `/run/mosaiq/jwks.json` | `0444`, root |
| `/etc/mosaiq/svc/auth.env` | `AUTH_KID`, `AUTH_PRIVATE_KEY_PATH`, `AUTH_ISSUER` and the rest of auth's configuration | `0600` |

Neither the key nor the JWK Set is in the repository (`*.pem` is git-ignored;
`git ls-files '*.pem'` lists nothing). `AUTH_KID` must be the same in
`auth.env` and in the unit's environment: the auth compose project builds the
key's mount path from it, so the unit reads `/etc/mosaiq/svc/auth.env` as an
`EnvironmentFile` as well as handing it to the container. auth refuses to start
when the private key's public half is not in the JWK Set under `AUTH_KID`, so a
mismatch stops the service instead of issuing tokens no one can verify.

The JWK Set is a bind-mounted single file. Always change it **in place**
(`tee`, `cat > file`), never by writing a new file and `mv`-ing it over: a
rename gives the file a new inode and every running container keeps reading
the old one.

## First key

Pick a `kid` that cannot be mistaken for another, such as the month: `2026-10`.

```bash
kid=2026-10
sudo install -d -m 0755 /etc/mosaiq/keys
sudo sh -c "umask 077 && openssl genrsa -out /etc/mosaiq/keys/auth-$kid.pem 2048"
sudo chown 10001:10001 /etc/mosaiq/keys/auth-$kid.pem
sudo chmod 0400 /etc/mosaiq/keys/auth-$kid.pem
```

The JWK Set, from a checkout of this repository (the helper uses only the
standard library and `openssl`):

```bash
python3 tests/conformance/tokens.py jwks /etc/mosaiq/keys/auth-$kid.pem $kid \
  | sudo tee /etc/mosaiq/keys/jwks.json >/dev/null
sudo chmod 0444 /etc/mosaiq/keys/jwks.json
```

Set `AUTH_KID=$kid` and `AUTH_PRIVATE_KEY_PATH=/run/mosaiq/auth-$kid.pem` in
`/etc/mosaiq/svc/auth.env`, then start the services.

## Rotation

Rotate on a schedule you can repeat, and at once when the private key may have
been read by anyone else. Nothing here signs anyone out: refresh tokens do not
depend on the key, and an access token signed with the old key stays valid for
its 10 minutes.

1. **Make the next key.** As in "First key", with a new `kid`
   (`next=2026-11`). Do not touch `jwks.json` yet.

2. **Publish both keys.** Write the JWK Set with the old and the new public
   key, in place:

   ```bash
   { jq -c '.keys[]' /etc/mosaiq/keys/jwks.json
     python3 tests/conformance/tokens.py jwks /etc/mosaiq/keys/auth-$next.pem $next \
       | jq -c '.keys[]'
   } | jq -s '{keys: .}' | sudo tee /etc/mosaiq/keys/jwks.json >/dev/null
   ```

3. **Make every service reload it.** Send `SIGHUP` to each service container;
   a service also reloads on an unknown `kid`, at most once every 30 s, so a
   missed one catches up by itself.

   ```bash
   for c in $(docker ps --format '{{.Names}} {{.Label "com.docker.compose.project"}}' \
                | awk '$2 ~ /^mosaiq-/ && $2 != "mosaiq-infra" {print $1}'); do
     docker kill --signal=HUP "$c"
   done
   ```

   Check that each answers: `curl -s http://127.0.0.1:<port>/health/ready`.

4. **Switch the signer.** Set `AUTH_KID=$next` and
   `AUTH_PRIVATE_KEY_PATH=/run/mosaiq/auth-$next.pem` in
   `/etc/mosaiq/svc/auth.env` and restart auth's unit
   (`sudo systemctl restart mosaiq-svc@auth`). The restart recreates the
   container with the new key mounted. New tokens carry the new `kid`; the old
   ones still verify, because the JWK Set holds both.

   ```bash
   curl -s https://<host>/api/auth/v1/jwks.json | jq '.keys[].kid'     # both
   # sign in, then read the header of the access token:
   cut -d. -f1 <<<"$ACCESS_TOKEN" | base64 -d 2>/dev/null | jq .kid     # $next
   ```

5. **Drop the old key after 10 minutes plus skew** (wait 15). Write the JWK
   Set with the new key only, in place, send `SIGHUP` as in step 3, and
   delete the old private key:

   ```bash
   jq --arg k "$next" '{keys: [.keys[] | select(.kid == $k)]}' /etc/mosaiq/keys/jwks.json \
     | sudo tee /etc/mosaiq/keys/jwks.json.new >/dev/null
   sudo sh -c 'cat /etc/mosaiq/keys/jwks.json.new > /etc/mosaiq/keys/jwks.json && rm /etc/mosaiq/keys/jwks.json.new'
   sudo shred -u /etc/mosaiq/keys/auth-<old>.pem
   ```

### Rolling back

Until step 5, put `AUTH_KID` back to the old value and restart auth's unit: the
old key is still in the JWK Set and on disk. After step 5 the old key is gone;
make a new one and rotate forward.

### When the private key leaked

Do steps 1 to 4 at once, then drop the old key immediately instead of waiting:
every token signed with it stops verifying within the services' reload, which
signs everyone out of the access tokens (10 minutes at most) and keeps refresh
tokens working. If the refresh tokens may have leaked as well, also end every
session: `redis-cli --user ops` on `redis-state`, `DEL` each `pub:auth:v1:live:*`
key found with `--scan` (an operator may scan; the services may not).

## NGINX on the instance

`services/auth/nginx/auth.conf` is the auth include of the 443 server block of
`deploy/nginx/mosaiq.conf` (applied by hand, see `deploy/README.md`). It needs
what that file's `http` context already declares, and `nginx -t` fails without:

- the zone `mosaiq_auth_signin` (`limit_req_zone`), keyed on the map
  `"$request_method:$uri"`, so only `POST /api/auth/v1/sessions` counts and the
  refresh does not;
- the maps `$mosaiq_error_format`, `$mosaiq_correlation_id` and the variable
  `$mosaiq_script_sources`, which the 413 and 429 bodies use.

The include's `error_page 429` answers the `rate_limited` envelope with
`Retry-After`. auth's own 429 (five failures of one email and IP, locked for
15 minutes) comes from the service with its own `Retry-After`, because NGINX
cannot know it. After changing the include:

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<host>/api/auth/v1/sessions \
  -H 'Content-Type: application/json' -d '{}'     # 400, not 502
```
