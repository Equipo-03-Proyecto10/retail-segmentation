# ADR-0091 — The web keeps its files in a private GCS bucket behind a file-store port, and only their metadata in PostgreSQL

**Status:** Accepted (2026-11-24)
**Owner:** Marcelo
**Issue:** #1189
**Supersedes:** —
**Superseded by:** —

---

## Context

The web stores a product's image on the instance's disk, under `UPLOAD_DIR`, and keeps the
file's name in `product.image_path` (RF-08, F3-07). Three things now make that insufficient.

- **The final delivery requires it.** Its instructions ask for files in Cloud Storage buckets,
  metadata-only rows in the database, and signed URLs for private files. A file on the
  instance's disk dies with the instance and is invisible to anything else.
- **C-1 and ADR-0045 do not say whether the web may call the GCS API.** C-1 forbids the
  web from *exposing* an external REST, GraphQL or SOAP API; the first delivery's scope also
  lists "external APIs" as out of scope, and AGENTS.md says the web "connects to no Redis and
  consumes no microservice". Reading those sentences as "the web never makes an outbound
  call to Google" is possible, so the exception has to be written down rather than
  inferred. What those rules protect is that the web is one application whose business
  data is in PostgreSQL and that it takes nothing from the services. A bucket holding
  opaque bytes is none of the three: it is not a business API, not a service, and not
  a second source of business facts.
- **The local stack and the tests have no cloud.** There is no Docker on a developer's
  machine here, no emulator in CI, and the real bucket is not reachable from either. Whatever
  talks to GCS needs a stand-in that behaves the same at the boundary.

What is genuinely uncertain: how the browser should reach a file while the bucket is not
public (stream it, redirect to a signed URL, or put a CDN in front); which role is the least
that works; and whether the instance's service account carries the OAuth scopes that let it
write at all (see "What must now be true elsewhere"). Signed URLs for private files are
the follow-up story #1190, and this record shapes the storage so that story needs no
schema change beyond what it names.

## Decision

**The web may call the Google Cloud Storage API, and nothing else may.** This is a scoped
exception to C-1 and to the reading of ADR-0045 above: Cloud Storage is an infrastructure
store, like PostgreSQL, not a business API and not a microservice. The exception covers
the web's file store and the operator's migration script, and no other outbound call. No
service calls GCS: a service that needs a file asks its owner's contract, and that is a
later decision.

**The web reaches the store through one port, `FileStore`** (`web/services/files.py`):
`put`, `get`, `delete`, each keyed by an object path. It has two backends. `GcsFileStore`
uses the official `google-cloud-storage` client, imported only when that backend is built,
so the local backend and the tests never load it. `LocalFileStore` writes under
`UPLOAD_DIR` and stands in for the local stack and for tests. `FILE_STORE_BACKEND` selects
one (`local` by default). In any `FLASK_ENV` other than `development` the web refuses to
start with the local backend, unless `FILE_STORE_ALLOW_LOCAL_IN_PRODUCTION=true` says that
is meant, so that a missing variable on the instance cannot silently put the files back on
its disk. The GCS backend refuses to start without `GCS_BUCKET`.

**Credentials are the instance's own.** The web authenticates with Application Default
Credentials, which on the instance resolve to its attached service account through the
metadata server. No key file exists anywhere: not in the repository, not on the disk, not in
`/etc/mosaiq/mosaiq.env`. That account holds `roles/storage.objectUser` (create, read,
update, delete and list objects) on the one bucket, not on the project;
`roles/storage.objectAdmin` is not needed, and a custom role of `storage.objects.create`,
`.get` and `.delete` is the narrower choice if the team wants exactly what the code uses.
V4 signing for #1190 uses IAM `signBlob` on that same account, which needs
`roles/iam.serviceAccountTokenCreator` on itself and the `cloud-platform` access scope; it
is not granted until #1190 ships.

**The bucket is private.** Uniform bucket-level access is on (no object ACLs), public access
prevention is enforced, and nobody holds `allUsers` or `allAuthenticatedUsers`. Its name comes
from `GCS_BUCKET`. An object is named `<kind>/<file_id>.<ext>`, for example
`product-image/6f1c…e2.jpg`: the kind, the file's own identifier and an extension chosen from
the validated content type. The user's file name is never used, stored or logged.

**A public file reaches the browser through the web.** The web's existing image route,
`GET /admin/products/image/<name>`, answers a name of the form `<file_id>.<ext>` by reading the
metadata row, checking the file is `PUBLIC` and `STORED` and that the extension matches, fetching
the object through the port and streaming it, with `Content-Type` from the row,
`X-Content-Type-Options: nosniff`, `ETag` set to the row's SHA-256, and
`Cache-Control: private, max-age=86400, immutable`. A matching `If-None-Match` answers 304 without
touching the bucket. The route keeps the permission it had (`catalog.read`); "public" names the
file's privacy class, not that the page is anonymous, and `private` keeps a shared cache such as
Cloudflare from storing it. The application's blanket `no-store` (#291) exempts exactly this
response. A `PRIVATE` file is never served by this route (it answers 404); #1190 gives it a V4
URL that expires within 15 minutes.

**The database keeps metadata only,** in one table, `stored_file` (schema in
`sql/01_schema.sql`): `file_id`, `object_path`, `content_type`, `size_bytes`, `sha256`,
`privacy` (`PUBLIC` or `PRIVATE`), `status` (`STORED`, `PENDING_DELETE`, `DELETED`),
`owner_user_id`, `created_at`, `deleted_at` and `metadata` (`jsonb`, free). CHECKs refuse a
content type other than the three image types, a size above 5 MB or not positive, a SHA-256
that is not 64 lowercase hex digits, a path that is not `<kind>/<file_id>.<ext>` with the
extension matching the content type, and a `deleted_at` that disagrees with the status.
`product.image_file_id` references it, unique where set, and a constraint trigger
(`product_image_file_matches`) refuses a product whose `image_path` is not the referenced
file's id plus the extension its content type implies (a CHECK cannot read the other row, so a
PNG could otherwise be published as `<id>.jpg`). A row is never deleted and its history cannot
be rewritten: the web's role may insert a row and update only `status` and `deleted_at`, a
trigger refuses any change to what the row says about its object, and the status only moves
forward (`STORED`, then `PENDING_DELETE`, then `DELETED`); `verify_app_role` checks the
privileges. The whole of this (the table, the column, the constraints, the triggers and the
privileges) is one re-runnable block of `sql/01_schema.sql`, `-- BEGIN FILE STORE`, so an
existing database is upgraded in place and a clean build gets the same objects.

**Writes put the bytes first, then the row.** The store is written, then the metadata row is
inserted in the same database transaction as the product. If the store refuses, no row is
written, the user sees a controlled error (HTTP 503 and a message on the form) and the failure
is logged. If the row or the product write fails, the transaction rolls back and the object
is deleted; if that delete cannot reach the store either, its metadata is committed in a fresh
transaction as `PENDING_DELETE`, so `--delete-pending` finds it and no object is ever left that
nothing records (the one window left is a process that dies between the two steps). The
reverse order would leave a row naming an object that does not exist, which a reader can
see. An edit locks the product's row and reads the picture it holds there, inside the same
transaction, and retires that one after it commits (a delete returns the picture it removed):
what is retired is what was displaced, not what the page read earlier, so two edits, or an
edit and a delete, cannot leave a stored but unreferenced file. An edit of a product deleted
meanwhile is refused and leaves no row and no object. The GCS calls carry a per-attempt timeout
of 10 seconds and a retry policy whose whole-operation deadline is 20 seconds, because the
client's default of 120 seconds would outlive NGINX's 60 and equal gunicorn's worker timeout.

**The existing images move once.** `deploy/migrate-uploads-1189.py` reads each `product` row
that has `image_path` and no `image_file_id`, uploads the file from `UPLOAD_DIR`, inserts its
row, and sets `image_file_id` and the new `image_path` in one transaction. It is idempotent and
resumable (a finished product is no longer selected), supports `--dry-run`, parameterizes
every statement, and lists every file it could not move, with the reason. It attributes the
migrated rows to the administrator, because the old upload did not record who made it. It
does not delete the originals; the operator removes them after checking the report. Until it
has run, the web keeps serving a not-yet-migrated image from `UPLOAD_DIR` through the same route.
A file that cannot be read for any reason (missing, a directory, a permission) is listed with
its reason like any other, and does not stop the rest. **The live database is not rebuilt before
it runs**: the clean seed has no product pictures, and `product.image_path` is the only record of
which product shows which file, so the schema block above is applied to the live database, the
web is released, and the script runs, before any later re-provision.

**`product.image_path` stays, and is derived.** products-service publishes `photo_url` as
`PRODUCTS_PHOTO_BASE_URL` joined to `image_path`, and its contract is frozen: clearing the
column after the move would silently null every migrated photo in a released service. So a
picture in the store is named `<image_file_id>.<ext>` in `image_path`, and `/admin/products/image/<name>`
resolves that name to the file. The column is then derivable from `image_file_id` and the
content type, a deliberate and temporary departure from 4NF that the schema checks
(`product_image_name_check`) instead of trusting. The story that gives products-service a
published view of the picture drops the column.

**When the bucket is unreachable,** an upload is refused as above; a read answers 503 with
the common error page (the product page still renders, the picture does not); and a delete
does not fail the user's action: the row is committed `PENDING_DELETE` before the store is
touched, then `DELETED` once the object is gone, so an interruption or a failure at any step
leaves a row that says what is still to do and never `STORED` over a missing object. The
migration script's `--delete-pending` mode finishes every `PENDING_DELETE` row.

### Why `stored_file` is in 4NF

Every non-key attribute depends on `file_id` and on nothing else, and `object_path` is a second
candidate key (the same object name cannot belong to two rows). The one dependency a reader
might propose is `sha256 → size_bytes, content_type`: equal bytes have an equal size. It is
not a dependency of the schema, because `sha256` is deliberately not a key: two uploads of the
same picture are two files, each with its own owner, privacy, status and life, so the
columns describe one stored object, not one distinct content. There is no multivalued
dependency, because the table holds no two independent sets about one file (the owner, the
privacy and the status are single-valued; a product's image is one file, and a gallery would
be its own association table, not a repeating column). `metadata` is one opaque attribute
for facts nothing queries or joins on; a fact a query needs becomes a column, so the
`jsonb` never holds a repeating group. The path's prefix (the kind) is recorded once, in the
path, not in a second column. The one departure is `product.image_path` above, named and checked.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep the files on the instance's disk | They do not survive the instance, and the final delivery requires Cloud Storage |
| A `files-service` microservice the web calls | The web consumes no service (AGENTS.md); a sixteenth deployable unit for opaque bytes is not one business rule; it would also put the web's own upload behind a network hop it does not need |
| Objects public-read, the browser loads them from `storage.googleapis.com` | Public access prevention is the guarantee the story asks for, and a public object cannot be withdrawn from a URL that has been shared |
| Redirect each public image to a V4 signed URL | Every render would sign, so the URL changes each time and the browser cannot cache the image; it also needs `signBlob` on day one. Signed URLs are kept for the private files, where expiry is the point |
| A service-account key file on the instance | A secret on a disk and in an environment file, to rotate and to leak; the attached account makes the key unnecessary |
| `fake-gcs-server` or the storage emulator for the local stack | Needs Docker, which is not available on every machine and not in the test job; the local backend behind the same port tests the same code paths without a second process |
| Call the GCS client directly from the routes, with no port | Every test would need the cloud or a patched client, and the failure ordering above could not be tested without it |
| Name objects after the uploaded file | A user-chosen name is a path-traversal and collision risk and is personal data in a log line |
| Keep the bytes in PostgreSQL (`bytea`) | The story forbids it, and the instance's database backups would carry every image |

## Consequences

**What this makes easy.** The files outlive the instance. The web's tests exercise the full
upload, read and delete paths with no cloud. #1190 adds a signed URL for `PRIVATE` rows and a
route for them; the table, the naming and the credentials are already in place.

**What this makes hard.** The web now has one outbound dependency. When the bucket is down,
nobody can upload and product pictures do not load, although every other page works. A crash
between the store write and the database write leaves an object with no row, and nothing
sweeps those yet. The `content_type` CHECK lists the three image types: the first story that
stores another kind (a PDF, a CSV) widens it in `sql/01_schema.sql`, which is a schema
change on the live database. `FLASK_ENV=production` with no store configured now refuses to
start.

**What must now be true elsewhere.**
- The bucket exists, private, with uniform bucket-level access and public access prevention,
  and its name is in `GCS_BUCKET`. The operator creates it; this change creates nothing
  in the cloud.
- The instance's service account holds the bucket-scoped role above, and the VM carries an
  access scope that lets it write to Cloud Storage. A VM created with the default scopes
  has only `devstorage.read_only`, so writes fail with 403 until the scope is changed, which
  needs the VM stopped. `docs/infra.md` records the final scope.
- `/etc/mosaiq/mosaiq.env` sets `FILE_STORE_BACKEND=gcs` and `GCS_BUCKET` before the release
  that contains this record is deployed, or the web refuses to start and the deploy script
  rolls back.
- The `FILE STORE` block of `sql/01_schema.sql` is applied to the live database before the web
  release, without rebuilding it, and the migration script runs once afterwards, following
  `deploy/README.md`.
- `google-cloud-storage` is pinned in `web/requirements.txt` and installed in the instance's
  virtualenv.
- `docs/scope.md` C-1 is read with this exception; AGENTS.md's line that the web "consumes no
  microservice" is unchanged.

## Compliance

- `tests/test_file_store.py` runs both backends, the GCS one against a fake client that
  records its calls. `tests/test_file_store_postgres.py` runs the upload, read, replace and
  delete paths through the web's routes against a real PostgreSQL, on each backend, and covers
  both failure orders (store down: no row; row refused: object removed).
- `tests/test_stored_file_schema.py` shows each CHECK of `stored_file` and of
  `product_one_image_source` refusing a bad row, and the seeded history rows.
- `tests/test_migrate_uploads.py` runs the script's dry run and real run against a temporary
  `UPLOAD_DIR`, twice, and with a file it cannot move.
- `grep -rn "google.cloud" web/ deploy/ services/` finds the import only in
  `web/services/files.py`, and nothing under `services/` or `deploy/`.
- `Config.from_env` raises `ConfigurationError` for the local backend outside
  `development`, which `tests/test_config.py` asserts.
