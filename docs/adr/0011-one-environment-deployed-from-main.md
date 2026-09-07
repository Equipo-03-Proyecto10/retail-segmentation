# ADR-0011 — The instance is one environment, deployed automatically only from `main`

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #90 (F6-06)
**Supersedes:** —
**Superseded by:** —

---

## Context

F6-06 asks for the assigned host to be updated automatically when code reaches
`main`, so that what is published is what has been reviewed rather than whatever
someone last copied over SSH. The starting position makes the case: the instance
was serving `8de6e61`, a `develop` commit roughly forty commits behind, and
nothing recorded that it had drifted. A manual deploy step is not merely
laborious — it is invisible when it does not happen.

The delivery has exactly one host (ADR-0001, `docs/scope.md` §3): one GCP
instance running the application, PostgreSQL and NGINX together. There is no
staging instance and no budget conversation that would produce one. So the
question is not *which* environments deploy where, but whether a single
environment should be driven from `main`, from `develop`, or from both.

What is genuinely a judgement call: with one host, continuous deployment from
`develop` would give the team faster feedback on a real instance, at the cost of
the published site reflecting unreviewed integration work at arbitrary moments.
The project is graded partly on what a visitor sees at the published URL, which
is what settles it — but on a project with a staging box the answer could
reasonably go the other way.

## Decision

The instance is a single **production** environment, and the only thing that
deploys to it is the `Deploy` workflow, triggered by a push to `main`. `develop`
never deploys. A deploy checks out the exact merged commit, installs
dependencies, restarts the service and verifies the site answers; if any of that
fails it restores the commit that was serving before and verifies again, so a
failed deploy is never left live. Deploys authenticate through Workload Identity
Federation, restricted to this repository and to `refs/heads/main`, so no
long-lived credential exists to be leaked or rotated.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Deploy from `develop` | The published URL is part of the delivery. `develop` is the integration branch and is expected to hold work that is merged but not yet released; pointing the public site at it means a reviewer can arrive mid-integration. The team also has no second host to fall back on while `develop` is broken. |
| Deploy from both, to one host | Whichever branch pushed last wins, so the instance's state becomes a race rather than a decision. |
| Keep deploying by hand over SSH | This is the status quo the story exists to remove, and its failure mode is already recorded above: the instance quietly fell forty commits behind and nobody could tell from the repository. |
| A service-account JSON key in a repository secret | Simpler to set up, but it is a long-lived credential with SSH access to the production host, living in repository settings and valid until somebody remembers to rotate it. WIF removes the credential rather than protecting it. |
| Blue/green or two gunicorn slots behind NGINX | The correct answer for zero-downtime, and disproportionate here: one 2-vCPU instance, one application, and a delivery that tolerates a few seconds of restart. Recorded as the upgrade path if downtime ever becomes a real constraint. |

## Consequences

**What this makes easy.** The published site is, by construction, the reviewed
and integrated code: `main` is protected, so the only route onto the instance is
a reviewed pull request. Releasing becomes a merge, with no runbook step that
can be skipped. The deploy is reproducible — it checks out a commit SHA, not a
branch tip — so re-running it is deterministic, and `git rev-parse HEAD` on the
instance names exactly what is serving.

**What this makes hard.** There is nowhere to try a deploy before it is real: a
change that only breaks on the instance breaks production, briefly, before the
rollback returns the previous version. The window is a restart, not zero — the
guarantee is that a broken deploy is not *left* serving, not that nobody sees
it. Releasing now also requires a `develop` → `main` pull request, which is
ceremony the team did not previously have.

**The database is deliberately outside this.** A deploy never runs SQL. The
three scripts in `sql/` are built to run clean against an *empty* database and
are not migrations; running them against the live one would be destructive. A
release that needs a schema change therefore needs a person, and that is a known
gap rather than an oversight — `docs/roadmap.md` is where a migration tool would
be proposed if the delivery ever needs one.

**What must now be true elsewhere.** `main` stays protected and reachable only
by reviewed pull request, or the security of this pipeline is the security of
whoever can push. The instance's checkout at `/opt/mosaiq/current` must remain a
clone with `origin` pointing at this repository, and the service unit, the venv
path and the health endpoint must keep matching `deploy/deploy.sh`. The Workload
Identity Federation pool, its provider and the `mosaiq-deploy` service account
must continue to exist in project `iac-dev-01`; they are recorded in
`docs/infra.md` because they are not reproducible from this repository alone.

## Compliance

```bash
# The workflow deploys from main and nothing else.
grep -A2 '^on:' .github/workflows/deploy.yml | grep -q 'branches: \[main\]'

# No long-lived credential: authentication is federated, and no secret is read.
grep -q 'workload_identity_provider' .github/workflows/deploy.yml
! grep -qi 'secrets\.' .github/workflows/deploy.yml

# Impersonation is restricted to main at the IAM layer, not only in the workflow.
gcloud iam service-accounts get-iam-policy \
  mosaiq-deploy@iac-dev-01.iam.gserviceaccount.com --project=iac-dev-01 \
  --format='value(bindings.members)' | grep -q 'attribute.ref/refs/heads/main'

# The deploy never touches the database.
! grep -qE 'psql|sql/' deploy/deploy.sh
```

The rollback behaviour is verified by deploying a deliberately broken commit and
observing the previous one restored; the evidence is attached to #90.
