# Architecture Decision Records

One decision per file, numbered in the order the decision was opened:
`NNNN-kebab-case-title.md`. A number is never reused.

**Immutable once Accepted.** An accepted record is never edited to change what
it decided. If the decision changes, write a new record that supersedes it and
set the old one's status to `Superseded by ADR-NNNN`. That status line is the
only edit an accepted record ever receives; fixing a typo or a broken link is
fine, changing the decision is not.

The point of the rule is that the record explains why the system is the way it
is *at the moment somebody decided it*, including the constraints that applied
then. Editing it to match what the team learned later destroys exactly the
information a reader needs.

**Decisions are not made silently in code.** A pull request that establishes a
constraint other work must respect needs an ADR, or a reference to one.

## Statuses

| Status | Meaning |
|---|---|
| `Proposed` | Written, under review, not binding until accepted before merge |
| `Accepted` | Binding, and immutable from this point |
| `Rejected` | Considered and declined. Kept, because the reasoning is worth as much as an acceptance |
| `Superseded by ADR-NNNN` | Replaced. The successor states what changed |

Acceptance is a status transition, not a forbidden edit to an accepted
record: a proposal becomes immutable when it is accepted. The ADR owner
proposes acceptance in a PR; the approving team reviewer confirms it before
merge. A proposal may accompany its implementation for review, but must be
accepted before that implementation is merged.

The #165 reconciliation records ADR-0003 through ADR-0013 as Accepted because
their implementations already passed the team's merge process. Their decision
text is unchanged. [Review evidence](../evidence/review-findings-resolution.md)
records the execution checks for ADR-0006 and the acceptance basis for the set.
PR #168 introduced a second ADR numbered 0012 while this repair was in
progress. Its still-proposed Cloudflare record is numbered 0013 here, with
references corrected and acceptance reconciled against that merged PR. The
original release-merge ADR keeps number 0012.
ADR-0014 is new and awaits acceptance with this PR.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-flask-monolith-on-a-single-vm.md) | Flask monolith on a single Compute Engine instance | Accepted |
| [0002](0002-mosaiq-identity-and-design-system.md) | MOSAIQ identity and a token-based design system | Accepted |
| [0003](0003-layered-architecture-with-an-explicit-service-layer.md) | Layered architecture with an explicit service layer, rather than classic MVC | Accepted |
| [0004](0004-model-ahead-of-the-deferred-segmentation-modules.md) | The PostgreSQL model carries the deferred segmentation tables now | Accepted |
| [0005](0005-document-mongodb-and-redis-designs-without-implementing-them.md) | MongoDB and Redis are documented as designs and not implemented | Accepted |
| [0006](0006-run-under-both-systemd-and-docker-compose.md) | The application runs under both systemd and Docker Compose, with systemd the default on the instance | Accepted |
| [0007](0007-permissions-in-code-with-a-default-deny-middleware.md) | Permissions are declared in code and enforced by a default-deny middleware | Accepted |
| [0008](0008-the-instance-keeps-the-demonstration-accounts-deactivated.md) | The instance keeps the demonstration accounts, deactivated | Accepted |
| [0009](0009-nginx-as-the-reverse-proxy.md) | NGINX is the reverse proxy in front of the application | Accepted |
| [0010](0010-the-consultation-module-is-a-separate-read-only-blueprint.md) | The consultation module is a separate read-only blueprint, gated by two existing permissions | Accepted |
| [0011](0011-one-environment-deployed-from-main.md) | The instance is one environment, deployed automatically only from `main` | Accepted |
| [0012](0012-release-merges-preserve-ancestry.md) | `develop` and `main` are joined only by merge commits, never by squash or rebase | Accepted |
| [0013](0013-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md) | MOSAIQ is published through Cloudflare with an origin certificate | Accepted |
| [0014](0014-service-owned-transactions-and-typed-write-failures.md) | Services own transactions and translate typed write failures | Proposed |

## Writing one

Copy [`template.md`](template.md), number it, open a pull request. Keep it
short — an ADR that takes twenty minutes to read does not get read, and the
decision gets re-litigated in a pull request weeks later.

State the alternatives you rejected and why. A record with no rejected
alternatives has recorded a preference, not a decision.
