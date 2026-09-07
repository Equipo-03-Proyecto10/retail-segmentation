# F6-06 — Continuous deployment to the instance

Evidence for [#90](https://github.com/Equipo-03-Proyecto10/retail-segmentation/issues/90).
Decision: [ADR-0011](../adr/0011-one-environment-deployed-from-main.md).
Procedure: [`deploy/README.md`](../../deploy/README.md).

The claim worth evidencing is not that a deploy works when everything goes
right. It is that a deploy which goes *wrong* does not leave the instance
broken — the third acceptance criterion, and the one that cannot be evidenced
by asserting it.

## How this run was produced

`mosaiq-deployment-vm` (project `iac-dev-01`, zone `northamerica-south1-a`),
2026-09-07 UTC, driven from a workstation over `gcloud compute ssh`. The
`deploy/deploy.sh` under test is the one in this pull request.

## The state this story found

```
commit:  8de6e618cb76ddd29e1a4fbf948e97428b891045
branch:  develop
https 200
```

A `develop` commit roughly forty behind, on the host that publishes the
delivery, with nothing in the repository recording that it had drifted. That is
the argument for the story: a manual deploy step is not merely laborious, it is
invisible when it does not happen.

## AC 1 — a deploy updates the running application

**Dry run**, which resolves the target and changes nothing:

```
=== Deploy requested: 71428a3
currently serving: 8de6e618cb76ddd29e1a4fbf948e97428b891045
deploying:         71428a31e6928451394a42a1d9b65180d4075659

=== Dry run — nothing was changed
71428a3 feat(app): read-only consultation module … (#65) (#135)
56ba28e feat(deploy): loopback-only PostgreSQL and least-privilege application role (#52, #53) (#134)
…
healthy after 1 attempt(s): HTTP 200
```

**A real deploy**, end to end on the live instance — checkout, dependency
install, service restart, health check through NGINX:

```
=== Deploy requested: 71428a3…
currently serving: 8de6e618cb76ddd29e1a4fbf948e97428b891045
deploying:         71428a31e6928451394a42a1d9b65180d4075659

=== Checking out …
=== Installing dependencies
=== Restarting mosaiq
=== Health check
healthy after 1 attempt(s): HTTP 200

=== Deployed 71428a3
● mosaiq.service - MOSAIQ web application (gunicorn)
     Active: active (running) since Mon 2026-09-07 01:17:43 UTC
```

This exercised the deploy mechanism, not the pipeline that will invoke it. The
workflow run itself is pending — see "Not evidenced here".

## AC 2 — the single-environment decision is recorded

[ADR-0011](../adr/0011-one-environment-deployed-from-main.md): the instance is
one production environment, `main` is the only branch that deploys to it, and
`develop` never does. The record keeps the alternative a reasonable person
would pick — deploying from `develop` for faster feedback on a real host — and
states what settles it: the published URL is part of the delivery, and there is
no second host to fall back on while `develop` is broken.

## AC 3 — a failed deploy leaves the previous version serving

The probe is a real broken release, not a disabled health check. `198eb6d` on
`test/90-rollback-probe` is the landing page with a `RuntimeError` raised at the
top of the view: gunicorn starts cleanly and the site then answers 500, which is
the failure mode that `systemctl is-active` cannot see and a health check can.

Faking the check instead would have proved nothing — the rollback path runs the
same function, so a forced failure would also make the recovery look like it
failed.

```
=== Deploy requested: 198eb6d8f769864544c05fb7bbc299782c7229ec
currently serving: 71428a31e6928451394a42a1d9b65180d4075659
deploying:         198eb6d8f769864544c05fb7bbc299782c7229ec

=== Checking out 198eb6d8f769864544c05fb7bbc299782c7229ec

=== Installing dependencies

=== Restarting mosaiq

=== Health check

=== FAILED — rolling back to 71428a31e6928451394a42a1d9b65180d4075659
unhealthy after 4 attempts: HTTP 500
healthy after 1 attempt(s): HTTP 200
rolled back; the previous version is serving again
```

The script exits 70, so the caller fails too rather than reporting a green
deploy. `unhealthy after 4 attempts` appears below the `FAILED` banner because
the failure line goes to stderr and the banners to stdout; the two streams
interleave when captured together.

State immediately afterwards — the broken commit is gone and the instance is
back where it started, with no human intervention:

```
commit:  71428a31e6928451394a42a1d9b65180d4075659
service: active
internal: https 200
71428a3 feat(app): read-only consultation module for products, customers, stock and segments (#65) (#135)

$ curl -skI https://34.51.123.31/ | head -1
HTTP/2 200
```

## A near miss worth recording

The first attempt at this test reported `healthy` and deployed the *good*
commit. The terminal did not handle bracketed paste and inserted its markers
literally, leaving a trailing `~` on the target SHA. In git, `<sha>~` means the
*parent of* that commit — the parent of the broken probe is the good commit, so
the deploy silently retargeted, succeeded, and was right to say so.

Two things caught it, and both are worth keeping. The script prints
`deploying:` with the SHA it actually resolved, which is where the substitution
was visible. And the procedure in `deploy/README.md` now resolves the SHA with
`git rev-parse` and compares before running anything destructive.

The cost was that the instance moved from `8de6e61` to the `develop` tip
earlier than planned. No harm — that commit is reviewed and integrated, and it
became the rollback target for the real test above.

## Security posture

No credential is stored in this repository or its settings. Authentication is
Workload Identity Federation; GitHub mints an OIDC token per run and GCP
exchanges it for short-lived credentials. The ADR's compliance block, run:

```
PASS deploys from main only
PASS federated auth
PASS no repository secret is read
PASS impersonation restricted to main
PASS deploy never runs SQL
```

Impersonation is restricted twice: the provider accepts tokens only from this
repository, and only tokens whose ref is `refs/heads/main` may impersonate
`mosaiq-deploy`. Grants are instance-scoped (`roles/compute.osAdminLogin`,
`roles/compute.viewer` on the one VM) plus a custom project role holding the
single permission `compute.projects.get`. Full table in
[`docs/infra.md`](../infra.md).

## AC 1, through the pipeline

The first merge to `main` deployed, on run
[34076420600](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/34076420600):

```
--- login profile this run will use ---
registered keys: 2

=== Deploy requested: e4da24dac914b95ed7001d79c06f13f5b5a87b3e
currently serving: 71428a31e6928451394a42a1d9b65180d4075659
deploying:         e4da24dac914b95ed7001d79c06f13f5b5a87b3e

=== Checking out e4da24dac914b95ed7001d79c06f13f5b5a87b3e
=== Installing dependencies
=== Restarting mosaiq
=== Health check
healthy after 1 attempt(s): HTTP 200

=== Deployed e4da24d
     Active: active (running) since Mon 2026-09-07 02:31:42 UTC
```

Afterwards `main` and the instance agree:

```
main tip:  e4da24d
commit:    e4da24dac914b95ed7001d79c06f13f5b5a87b3e
service:   active
internal:  https 200
public:    HTTP/2 200
```

The instance left `71428a3`, where a manual run had parked it, and now serves
exactly what was reviewed and merged.

## What it took, which is the part worth keeping

Four runs failed first, each on a different cause. Federation itself worked on
the very first attempt — every failure was downstream of it, and every one was
safe: each happened before `deploy.sh` ran, so the instance never changed.

| Run | Failure | What it actually meant |
|---|---|---|
| 1 | `PERMISSION_DENIED: … iam.serviceAccounts.actAs` | Reaching an instance means acting as the identity it runs as, so the caller needs `roles/iam.serviceAccountUser` on the instance's own service account — a grant separate from the login roles. |
| 2 | the same error | IAM propagation. The grant was already correct; the re-run was about a minute early. |
| 3 | `Permission denied (publickey)` | `roles/compute.osAdminLogin` was bound on the instance. OS Login created the login profile — the POSIX user resolved through NSS — but served no keys for it. Granting the role on the project fixed it. |
| 4 | `dest open "/tmp/mosaiq-deploy.sh": Permission denied` | A file left by manual testing, owned by another user. `/tmp` is sticky, so the upload could not overwrite it. |

Failure 3 named nothing useful. It was found on the instance by asking OS Login
for the service account's keys and comparing against a human account:

```
### keys for the deploy service account's user
  (nothing)
### the same command for a human account, as a control
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCgCLR6NNvD4LURBEY0+FJX
…
```

Two changes came out of this. The workflow registers its SSH key with OS Login
as its own step, so that class of failure names the missing permission instead
of surfacing two steps later as a bare rejection. And it uploads to a path
unique per run attempt, so a leftover file cannot block it again.

## Not evidenced here
- **Zero-downtime deployment.** Not claimed. One instance and one gunicorn mean
  a restart is visible, and the rollback above took a few seconds during which
  the site answered 500. ADR-0011 records the guarantee as "a broken deploy is
  not left serving", and blue/green as the upgrade path if that ever stops being
  enough.
- **Schema changes.** A deploy never runs SQL, by design. A release needing a
  schema change still needs a person.
