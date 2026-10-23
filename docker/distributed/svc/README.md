# Local service env files

`compose.distributed.yaml` reads `docker/distributed/svc/<name>.env` for each
service, where the instance reads `/etc/mosaiq/svc/<name>.env`. Each
implementation story commits its service's file here with safe local
defaults only, the same values `.env.example` documents:

```dotenv
DATABASE_URL=postgresql://svc_<suffix>@/retail?host=/run/postgresql
REDIS_STATE_URL=redis://redis-state:6379/0
REDIS_<SUFFIX>_PASSWORD=svc_<suffix>
AUTH_JWKS_PATH=/run/mosaiq/jwks.json
MOSAIQ_SERVICE_NAME=<name>
```

auth's file also sets `AUTH_ISSUER`, `AUTH_KID`, `AUTH_PRIVATE_KEY_PATH` and the
two TTLs. Its private key is never committed: run
`bash docker/distributed/auth-keys.sh` once before `docker compose up`; it
writes a throwaway pair and the JWK Set every local service mounts into the
git-ignored `docker/distributed/keys/`.

The local `redis-acl` step reads `REDIS_<SUFFIX>_PASSWORD` from the same file
to build the aclfile, as `deploy/infra/redis/assemble-acl.sh` does on the
instance.
