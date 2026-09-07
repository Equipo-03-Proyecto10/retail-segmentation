# ADR-0003 — The application is layered with an explicit service layer, rather than classic MVC

**Status:** Accepted
**Owner:** Marcelo
**Issue:** #61 (F3-01)
**Supersedes:** —
**Superseded by:** —

---

## Context

`docs/scope.md` requires the project to be "organized by layers" (§5,
deliverable 8) and lists a "layered project structure" among the Phase 3
contents (§4). Neither says which layers, and the justification of design
decisions is itself a graded deliverable (§5, deliverable 3). F3-01 (#61) had
to choose a structure to create the skeleton, so the decision could not be
deferred any further.

MVC is the alternative a reasonable person picks by default, and it is the
vocabulary most course material uses. Choosing anything else has to be
defended rather than assumed.

The comparison is less symmetrical than it looks, and that is the part worth
recording. MVC is a pattern for the presentation layer; a layered architecture
is a rule about the direction of dependencies across the whole application.
They are not two answers to the same question. The question this repository
actually has to answer is narrower: **where does business logic live, and what
does "model" mean here?**

Three constraints already fixed elsewhere shape that answer:

- **No ORM.** `web/requirements.txt` uses psycopg directly, on the grounds that
  the exercise grades a normalized schema and parameterized SQL and an ORM
  hides both.
- **Server-rendered HTML.** C-2 means a template is a pure function of the
  values a route passes it. There is no client-side state and nothing observes
  a model.
- **Test-first for business logic**, `docs/process.md` §4. The single
  administrator rule (C-4) is the first rule that has to be unit-tested with
  neither HTTP nor PostgreSQL in the way.

## Decision

The application is organized as layers, each calling only the one below it:
`routes` speak HTTP, `services` hold business logic and never touch a request,
`db` holds every SQL statement the application runs, and `templates` render the
HTML. The presentation is MVC-shaped — a route is the controller, a template is
the view — but what MVC calls the model is deliberately split in two, business
rules in `services` and data access in `db`.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Classic web MVC — `controllers/`, `models/`, `views/`, as Rails and Django name it | It leaves business logic without a home. With only controllers and models, a rule lands either in a controller, where it cannot be tested without a request context, or in a model, where it cannot be tested without a database. `docs/process.md` §4 commits the team to writing that test first, and C-4's single-administrator rule is the first place the cost would be paid |
| A `models/` package in place of `db/` | In MVC, "model" means an Active Record object supplied by an ORM. There is no ORM here on purpose, so the package would hold a bag of SQL functions under a name promising entity semantics that do not exist. It would also blur the rule that makes `AGENTS.md`'s parameterization requirement checkable: exactly one package may contain SQL, and a reviewer can grep for it |
| Keep this structure but rename it to MVC vocabulary | The names would be more familiar and less accurate. `views/` for Jinja2 templates invokes the observer relationship of Smalltalk MVC, which server-rendered HTML does not have, and `models/` reintroduces the ambiguity above. The mapping is recorded in `web/README.md` instead, so the report can present the structure in either vocabulary without the directory names lying about it |
| Decide nothing and let the structure emerge with F3-04 | The structure is what F3-01 exists to produce, and retrofitting it once the administrator CRUD is written is the expensive version of this decision. It is also the specific failure the story was written to prevent |

## Consequences

**What this makes easy.** Business logic is unit-testable without a request and
without a database — `tests/test_status.py` is the first instance and the
pattern every later service follows. The parameterization rule becomes a review
check rather than a hope, because SQL may appear in exactly one package.
Entry points that are not HTTP — F3-09's bootstrap command, and the analytics
deferred in `docs/roadmap.md` — call services directly instead of faking a
request.

**What this makes hard.** Trivial cases pay for the indirection: a listing
route still passes through a service that mostly delegates, and the temptation
to skip the layer "just this once" is real. Review has to hold that line,
because the layer stops being load-bearing the moment it is optional. The team
also pays a vocabulary cost — course material and the final report say MVC, so
the mapping has to be explained rather than assumed.

**What must now be true elsewhere.** `web/README.md` documents the layout and
the direction rule. F3-02 (#62) puts the connection in `web/db/` and nowhere
else. F4-01 (#69) puts the authorization middleware in front of the routes,
not inside them. F3-04 (#64) keeps its rules in `services` and its SQL in `db`.
A story that needs a layer this record does not describe changes the record,
not the code.

## Compliance

Three checks, in decreasing order of strength:

```bash
# 1. No service knows about Flask. Business logic stays independent of HTTP.
grep -rn "flask" web/services/ && echo "FAIL" || echo "ok"

# 2. No SQL outside web/db/.
grep -rniE "\b(select|insert|update|delete|create table|alter table)\b" \
  --include="*.py" web/ | grep -v "^web/db/" && echo "FAIL" || echo "ok"
```

3. Tests for business logic construct their inputs directly and never reach for
a test client. A service that can only be tested through `client.get()` has
lost the property this record exists to protect.
