# Wireframes

Low-fidelity wireframes for every screen in the delivery. They record the
**structural** design decisions — what is on each page, how a role reaches it,
what happens on submit — separately from the **visual** design, which is the
token-and-component system in [`../design-system/`](../design-system/) decided
in [ADR-0002](../adr/0002-mosaiq-identity-and-design-system.md).

Read them as the design step between the requirements and the templates:

```
requirements.md / user-stories.md   what each role must be able to do
        │
        ▼
docs/wireframes/                     structure and intent, one screen at a time   ← this folder
        │
        ▼
docs/design-system/                  type, colour, spacing, the 26 components
        │
        ▼
web/templates/*.html                 the Jinja2 implementation
```

Each screen is one self-contained HTML file (inline CSS, no assets, no
JavaScript) with a rendered PNG beside it. Open the `.html` in a browser, or
look at the `.png`. The right-hand panel of every sheet carries the route, the
roles, the requirements it serves, the template that implements it, and
numbered notes tied to callouts on the drawing.

**These artifacts change nothing functional.** They are documentation. The
screens they describe are already built; the wireframes are the record of why
each one is shaped the way it is.

## The screens

| # | Screen | Wireframe | Serves | Implemented in |
|---|--------|-----------|--------|----------------|
| 01 | Sign in | [`01-login.html`](01-login.html) · [png](01-login.png) | RF-01, RF-02 | `auth/login.html` |
| 02 | Home / role dashboard | [`02-dashboard.html`](02-dashboard.html) · [png](02-dashboard.png) | RF-04, F3-12 | `dashboard.html` |
| 03 | Consultation hub | [`03-catalog-index.html`](03-catalog-index.html) · [png](03-catalog-index.png) | RF-10, RF-11, RF-13 | `catalog/index.html` |
| 04 | List + search (pattern) | [`04-list-search.html`](04-list-search.html) · [png](04-list-search.png) | RF-10, RNF-18 | `catalog/products.html` and every other list |
| 05 | Record detail (pattern) | [`05-record-detail.html`](05-record-detail.html) · [png](05-record-detail.png) | RF-10, RF-13 | `catalog/customer_detail.html` |
| 06 | Admin list + row actions | [`06-admin-list.html`](06-admin-list.html) · [png](06-admin-list.png) | RF-05, RF-09 | `admin/users.html` |
| 07 | Create / edit form (pattern) | [`07-create-edit-form.html`](07-create-edit-form.html) · [png](07-create-edit-form.png) | RF-06, RF-09, RNF-07 | `admin/user_form.html` and every catalog form |
| 08 | Confirm destructive action | [`08-confirm-delete.html`](08-confirm-delete.html) · [png](08-confirm-delete.png) | RF-06, RF-07 | `admin/confirm_delete.html` |
| 09 | Segment run — the main process | [`09-segment-run.html`](09-segment-run.html) · [png](09-segment-run.png) | RF-12 | `segment_run/index.html` |
| 10 | Confirm recalculation | [`10-segment-run-confirm.html`](10-segment-run-confirm.html) · [png](10-segment-run-confirm.png) | RF-12 | `segment_run/confirm.html` |
| 11 | Audit log — list + filters | [`11-audit-log.html`](11-audit-log.html) · [png](11-audit-log.png) | RF-14, RNF-17 | `audit/index.html` |
| 12 | Audit entry — before / after | [`12-audit-detail.html`](12-audit-detail.html) · [png](12-audit-detail.png) | RF-14 | `audit/detail.html` |
| 13 | Controlled error page | [`13-error-page.html`](13-error-page.html) · [png](13-error-page.png) | RF-15 | `errors/error.html` |

Screens 04, 05 and 07 are drawn once as patterns: the products / customers /
stock / segments / users lists share one structure, as do the catalog and user
forms. The remaining seven admin templates are instances of 04, 06, 07 and 08.

Every item on the technical demonstration list (`requirements.md` §4) has a
screen here: sign-in (01), role-differentiated access (02, 03, 06), catalog
operations (06–08), the main process (09–10), a query (04–05, 11), the audit
log (11–12). PostgreSQL storage and container execution are the two items with
no screen of their own.
