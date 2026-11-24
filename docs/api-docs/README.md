# API documentation page

The Swagger UI page published at `/api/docs/` (F29-16, #1192, ADR-0090). It
lists every service in `contracts/registry.yaml` and renders the contract that
service serves at `/api/<name>/openapi.yaml`.

| File | What it is |
|---|---|
| `index.html` | The page. It has no inline script. |
| `init.js` | Swagger UI's setup: the service list, the `/api/<name>` prefix for "Try it out", and no stored credentials. |
| `swagger-config.json` | Generated, so don't edit it. It is the service list: `python3 .github/scripts/api_docs_config.py`. |
| `vendor/` | `swagger-ui-dist` **5.34.1**, unmodified, with its `LICENSE` and `NOTICE` (Apache-2.0). |

## Where it is served

- Instance: `deploy/nginx/mosaiq.conf`, `location /api/docs/`, from
  `/opt/mosaiq/docs/api-docs/`, which `deploy/deploy.sh` already publishes
  with the rest of `docs/`.
- Local: the gateway of `compose.distributed.yaml`, at
  `http://127.0.0.1:8080/api/docs/`.

## Using "Try it out"

1. Open the `auth` contract, `POST /v1/sessions`, then **Try it out** with
   your own account.
2. Copy `access_token` from the response, then click **Authorize** and paste
   it.
3. Pick another service from the list at the top, then click **Authorize**
   again. Swagger UI clears the token whenever the service changes. The token
   is kept only in this tab's memory, and it is gone when you reload.

Example bodies don't appear. The contracts cite them by
`externalValue: examples/…`, but the services don't serve that folder, so type
the body yourself (`docs/evidence/1192-swagger-ui.md`).

## Updating Swagger UI

```bash
v=5.34.1   # a release at least two weeks old
curl -s https://registry.npmjs.org/swagger-ui-dist/$v | jq -r .dist.integrity
```

Download the tarball, check its `sha512` against that integrity value, then
copy `swagger-ui-bundle.js`, `swagger-ui-standalone-preset.js`,
`swagger-ui.css`, `LICENSE` and `NOTICE` into `vendor/`. Regenerate the
checksums with `shasum -a 256 … > vendor/SHA256SUMS`, then update the version
in this file and in `tests/test_api_docs.py`.

The 5.34.1 tarball's integrity is
`sha512-VSr8fXfI93obYaQicLJbLUKGRxDojj/SfRJDDLCioGTUBj4BK/Nj4eE9jCeCAnJxVLi7iS+08PbiZLyrQKBkJQ==`.
