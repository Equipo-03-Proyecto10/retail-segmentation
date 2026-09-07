# Navigation and product upload hotfix

The integration review of PR #132 found that the navigation still targeted
unregistered `catalog.index` and `users.index` endpoints. Both sections were
therefore hidden even though their administration views existed.

The menu now reaches `/admin/catalogs` and `/admin/users`. The catalog index
links to stores, categories, channels, products and roles. Catalog and user
forms mark only their own section current. Segments, campaigns and reports
remain hidden because their view endpoints do not exist.

The upload review also corrected relative-directory image retrieval, creation
with a rejected upload, cleanup after a database conflict, and committing the
product fields with the image path. Empty files and unsupported configured
MIME types are refused, and file deletion stays inside the upload directory.
Upload type validation checks the declared MIME type; it does not decode the
image content.

## Verification

- Python 3.12: `pytest -q` — 183 passed.
- `black --check .` and `ruff check .` passed.
- Regression tests use the real registered menu endpoints and cover role
  filtering, all five catalog links, and the active catalog/user section.
- Upload tests cover storage/retrieval, type and size rejection, empty files,
  deletion boundaries, and rejecting an upload before inserting a product.
- Chromium rendered the catalog index, new-user form and new-product form at
  375 and 1440 pixels without horizontal overflow. Rendered locally through
  Flask's test client with a mocked database; this is not deployment evidence.
- No schema or configuration changes in the hotfix. The upload settings
  already exist in `.env.example`.
