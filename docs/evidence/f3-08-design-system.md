# F3-08 — MOSAIQ design system across the interface

Evidence for the two acceptance criteria in issue #68: every user-facing
screen follows the chosen design system, and the interface remains usable at
375 px and 1440 px.

## What was applied

The CSS delivered in the MOSAIQ Design System archive is served from
`web/static/css/mosaiq/`. Its primitive, semantic and component tokens remain
unchanged from the accepted source in `docs/design-system/`. Application-only
composition lives in `web/static/css/app.css` and also uses the `--mq-*`
tokens; it does not introduce a second color or spacing vocabulary.

The shared Jinja shell now supplies the MOSAIQ navigation, top bar, theme
switch, permission-filtered destinations, current-page state, signed-in
identity and alert treatment. Reusable Jinja macros supply page headers,
status pills, empty states and pagination. The same component classes are used
by the public landing and sign-in pages, dashboard, catalog consultation,
administrator CRUD, segment run, audit log and controlled error pages.

Campaigns and Reports are named in the permission matrix but have no workflow
in this delivery. Each now has a permission-gated, server-rendered destination
that states `Still building`, so its navigation item never leads to a 404 or a
server error. No campaign or reporting behavior was added ahead of the scope.

## Responsive review

The key table screen and a planned-module destination were rendered through
the application factory with representative rows injected through its existing
test seam. This isolates the interface review from PostgreSQL while exercising
the shipped Flask routes, Jinja templates, static CSS, JavaScript and signed-in
session shell. Chromium reported no page-level horizontal overflow at either
width. Wide tables scroll inside their own bordered panel at 375 px, and the
horizontal navigation keeps the active destination in view.

| Screen | 1440 px | 375 px |
|---|---|---|
| Existing Products view | [Products at 1440 px](f3-08-products-1440.png) | [Products at 375 px](f3-08-products-375.png) |
| Campaigns planned-module view | [Campaigns at 1440 px](f3-08-coming-soon-1440.png) | [Campaigns at 375 px](f3-08-coming-soon-375.png) |

The Light and Dark controls were also exercised in Chromium. The selected
control updates `aria-pressed`, the root theme attribute and the semantic
surface colors.

## Automated verification

```text
pytest -q
678 passed

black --check .
61 files would be left unchanged

ruff check .
All checks passed!
```

The route tests additionally verify that every menu destination is registered,
Campaigns and Reports render the planned-module status page for authorized
roles, unauthorized roles remain refused, and the current navigation item is
marked on each section.
