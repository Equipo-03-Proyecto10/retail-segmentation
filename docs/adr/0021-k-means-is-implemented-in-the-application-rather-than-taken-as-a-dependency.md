# ADR-0021 — K-means is implemented in the application rather than taken as a dependency

**Status:** Accepted
**Owner:** Marcelo
**Issue:** #215 (F9-02)
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0018](0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md)
makes `KMEANS` one of the two run methods, and
[`scope-delivery-2-analytics.md`](../scope-delivery-2-analytics.md) §10 recorded
Q-10 as the open question it left behind: does the application take a
scientific-computing dependency such as scikit-learn, or fit the clusters
itself. F9-02 (#215) is the first story that cannot start without an answer.

The runtime today is five packages — Flask, psycopg, python-dotenv,
argon2-cffi and gunicorn. `web/requirements.txt` records that psycopg is used
directly rather than through an ORM because the exercise grades a normalized
schema and parameterized SQL, and an ORM hides both. The same reasoning applies
here: the exercise grades the segmentation, and a library that performs it is a
library that performs the graded part.

scikit-learn is not a small addition. It brings numpy and scipy, which are
compiled wheels, onto a CentOS 10 instance whose deployment is one systemd unit
that a person restarts, and into a Docker build that
[ADR-0015](0015-containers-are-a-development-path-only.md) keeps as a
development path only. It is also a general-purpose library where the work
needed is narrow: ADR-0018 already fixes the feature scaling, the initial
conditions that matter and the mapping from centroids to labels, so most of
what the library offers is not reachable through that contract anyway.

What is genuinely uncertain is quality and effort. A hand-written Lloyd's
algorithm is a well-understood piece of code, but it is still code the team
writes and tests rather than code that arrives already exercised by other
users. If the data outgrows it, or if the team later wants initialisation
strategies or quality measures beyond what it implements, this decision is the
one to revisit.

## Decision

The application implements K-means itself, in the service layer, and takes no
scientific-computing dependency: the `KMEANS` adapter performs Lloyd's
algorithm over the min-max normalized R, F and M features ADR-0018 specifies,
with k-means++ initialisation seeded from the run's recorded random seed, a
recorded iteration limit and convergence tolerance, and the resulting centroids
handed to ADR-0018's deterministic cluster-to-label mapping.
`web/requirements.txt` gains nothing, and the fit is reproducible from the
parameters the run already stores.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Add scikit-learn | It brings numpy and scipy as compiled wheels onto a single CentOS instance whose deployment is one systemd unit, for one algorithm whose inputs and outputs ADR-0018 has already fixed. It also performs the part of the work the exercise grades, which is the same objection `web/requirements.txt` already records against an ORM |
| Compute the clustering in PostgreSQL | There is no k-means in core PostgreSQL, so this means either a recursive query that is hard to read and harder to test, or an extension installed by hand on the instance. AGENTS.md forbids changing the server by hand, and an extension is a deployment dependency in the same way a Python package is |
| Use `RFM_RULES` only and drop `KMEANS` | ADR-0018 is Accepted and names both methods. Dropping one would need to supersede it, and it would remove the only segmentation the project derives from the data rather than from bands someone wrote |
| Take a smaller clustering library instead of scikit-learn | The dependency objection is about adding an unexercised third-party surface to a five-package runtime for a narrow, fixed algorithm. A less-used library keeps that cost and adds a maintenance risk scikit-learn does not have |

## Consequences

**What this makes easy.** The runtime stays at five packages, so the instance,
the Dockerfile and the deployment pipeline are untouched by Phase 9. The fit is
fully determined by values the run already records — seed, k, iteration limit,
tolerance, window — so reproducing a run needs nothing beyond the database. The
implementation can be tested directly against ADR-0018's cluster-id permutation
requirement without stubbing a library.

**What this makes hard.** The team owns the numerical code, including empty
clusters, non-convergence within the iteration limit, and ties between equally
distant centroids. Each needs a defined behaviour and a test, and each is a
place where a subtle bug produces plausible output rather than an error — the
same failure mode ADR-0018 exists to prevent one level up. The implementation
is single-threaded Python over the customer population, so a run costs more
than a compiled library would; the customer counts in this delivery make that
acceptable, and it is the first thing to measure if a run becomes slow.

**What must now be true elsewhere.** Q-10 in
[`scope-delivery-2-analytics.md`](../scope-delivery-2-analytics.md) §10 is
answered and moves out of the open list, and its §4 stack table records that
no dependency is added. F9-02 (#215) loses its blocking acceptance criterion
and gains the behaviours above. `web/requirements.txt` is unchanged, and a
pull request that adds numpy, scipy or scikit-learn to it contradicts this
record.
The clusters this produces still reach labels only through ADR-0018's
deterministic mapping; nothing here lets a raw cluster id into
`customer_segment_history`.

## Compliance

```bash
# No scientific-computing dependency enters the runtime or the dev tooling.
# Must print nothing.
grep -rniE 'scikit-learn|sklearn|^numpy|^scipy' \
    web/requirements.txt web/requirements-dev.txt pyproject.toml

# The adapter imports nothing outside the standard library and the application.
grep -nE '^\s*(import|from)\s+' web/services/segmentation.py \
  | grep -vE '\b(web|typing|dataclasses|math|random|statistics|collections|itertools|decimal|datetime)\b'
```

The fit's own correctness is checked by the tests F9-02 (#215) ships: a fixed
seed and fixed input produce the same partition on every run; a constant
feature maps to `0` rather than dividing by zero; an empty cluster and a
non-converging run each take their defined behaviour rather than raising; and
ADR-0018's permutation test proves the labels do not depend on the cluster ids
this code assigns.
