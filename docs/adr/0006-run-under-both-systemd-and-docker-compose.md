# ADR-0006 — The application runs under both systemd and Docker Compose, with systemd the default on the instance

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #89 (F3-09)
**Supersedes:** —
**Superseded by:** —

---

## Context

The first-partial delivery document's demonstration list ends with "ejecución
mediante contenedores", and its deliverable list names "Contenedores locales".
[`issue-history.md`](../issue-history.md) #20 retired the container stack —
"No containers — gunicorn under systemd on the instance" — and
[ADR-0001](0001-flask-monolith-on-a-single-vm.md) fixed that deployment.
[ADR-0005](0005-document-mongodb-and-redis-designs-without-implementing-them.md)
recorded the contradiction and left this half unanswered as Q-5 in
[`scope.md`](../scope.md) §8.

The deployment is not hypothetical. PostgreSQL is already installed on the
instance (#51, closed), F6-01 (#77) and F6-02 (#78) are written for NGINX in
front of gunicorn under systemd, and the delivery is days away. Replacing that
with containers would mean superseding ADR-0001, rewriting two deployment
stories, installing Docker on CentOS, and redoing work that already runs.

Doing nothing is not an option either: the demonstration item has no answer, and
a developer joining the team still has to install PostgreSQL by hand and run
three SQL scripts in the right order before anything works.

What is genuinely uncertain is whether maintaining two execution paths costs
more than it saves. Two ways to run the same application is two ways for it to
break, and the second one is exercised far less often than the first.

## Decision

The application runs under both systemd and Docker Compose, reading identical
configuration from the environment in either. **systemd is the default on the
GCP instance** and is what the delivery is deployed under; Docker Compose is the
default for local development and is what the demonstration's container item is
shown with, including on the instance when it is switched in for that purpose.
The two are alternatives and are never up at once.

This record does **not** supersede ADR-0001. The deployment decision stands
unchanged.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Containers everywhere, including the instance | One execution path is genuinely better, and this is the right answer for a project with more runway. It costs an ADR superseding ADR-0001, rewritten F6-01 and F6-02, Docker on CentOS, and discarding a working deployment — days before delivery, to satisfy a demonstration item that a local stack already satisfies |
| systemd only, and drop the container item | Leaves a listed demonstration item with no answer, and leaves a new developer installing PostgreSQL by hand. `docs/backlog.md` already carried F3-09 asking for one-command bootstrap; a shell script would have solved half of it and demonstrated none of it |
| Compose only, and skip systemd | The instance is the delivery. Running the graded artifact under the path exercised least is the wrong way round |

## Consequences

**What this makes easy.** One command gives a new developer a working
environment. The demonstration's container item has an answer that costs a
Dockerfile and a Compose file rather than a redeployment. The deployment already
underway is untouched, and so are #77, #78 and the PostgreSQL from #51.

**What this makes hard.** Two execution paths drift. A dependency added to the
image and not to the instance, or an environment variable set in Compose and not
in the unit file, breaks exactly one of them — and it will be the one nobody ran
today. `web/requirements.txt` is the single source both install from, and that
is the only thing keeping them aligned. Two paths also means two places to check
when something behaves differently, and the honest expectation is that the
container path will be the better-tested one locally and the worse-tested one on
the instance.

**What must now be true elsewhere.** Both paths bind `127.0.0.1:8000`, so NGINX
proxies to the same address either way and needs no change when the demonstration
switches modes — F6-01 (#77) must not assume otherwise. Neither path may carry a
connection string: both read `DATABASE_URL`, which is what lets the same image
talk to the `db` service locally and to the instance's own PostgreSQL there. The
systemd unit is F6-02's (#78) work and is not written by F3-09. Q-5 in
[`scope.md`](../scope.md) §8 keeps its MongoDB and Redis half open; only the
container half is answered here.

## Compliance

```bash
# Both paths install from the same file — this must be the only source.
grep -c . web/requirements.txt
grep -n 'requirements.txt' Dockerfile          # the image installs from it
grep -rn 'requirements.txt' docs/infra.md      # so does the instance

# Neither path carries a connection string.
grep -rniE 'postgresql://' Dockerfile compose.yaml   # must print nothing

# Both bind the same address, so NGINX is indifferent to which is running.
grep -n '8000' compose.yaml
```

The Compose path is exercised by F3-09's acceptance criteria; the systemd path
by F6-02's.
