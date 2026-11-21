# F22-12 (#808): the registry trains K-means, and what it costs

A training in `model-registry` reads the net purchases of a window, fits K-means with the
service's own code ([ADR-0021](../adr/0021-k-means-is-implemented-in-the-application-rather-than-taken-as-a-dependency.md))
and registers a `DRAFT` version. This records what that costs on the F23-01 evaluation
dataset, and what pins the fit to the web. **All the data is synthetic** (ADR-0052).

## How it was measured

On 2026-11-19, PostgreSQL 18, Python 3.12, a database built by the three scripts with the
evaluation dataset loaded by `python -m analysis.load` (seed 20261031: 5,000 customers and
51,954 sales; fingerprint `8a73b618d299d8abf08fd22d2cd10901b8222a0ca536bf015a77378e44e3826a`).
The seed adds 30 customers and 300 lines. Each training was a real `POST /v1/model-trainings`
through the service, polled until it ended, with the process's resident set read from
`/proc/self/status` before and after.

| Window | k | Lines read | Customers | Iterations | Time | Resident set grew by |
|---|---|---|---|---|---|---|
| 180 days | 6 | 22,858 | 4,009 | 25 (converged) | 0.9 s | 5.6 MB |
| 3,650 days | 8 | 52,554 | 5,030 | 24 (converged) | 1.3 s | 5.4 MB |
| 3,650 days | 2 | 52,554 | 5,030 | 9 (converged) | 0.8 s | 0.4 MB |

Both sets pass the 2,000-customer limit above which the silhouette is taken on a sample of
1,000 (the same limit and the same sample as the web), so the quadratic distance work never
runs on the whole set. The lines are read as a stream and added into one aggregate per
customer, so the memory held is the customers, not the lines.

- **What was and was not measured.** The resident set was read inside the process that ran
  the service's code and the test client, not in the container: it shows what a training
  adds (about 5 MB), not the container's total against its `mem_limit` of 128 MB. The
  service's idle size is the F19-08 measurement. A test (`test_training.py`) also bounds the
  Python allocations of a 3,979-customer fit under 24 MB.
- **One training at a time.** A second request while one runs is refused with
  `409 training_in_progress`, so the peak never adds two fits.

## What pins the fit to the web

`tests/golden/cases/model_registry.py` calls the web's own `fit_customers`, `normalise`,
`label_clusters` and `rank_clusters` on 15 inputs and `tests/golden/capture.py` writes
`services/model-registry/tests/golden/model-registry.json`. The service's port
(`app/kmeans.py`, `app/labels.py`) replays them in `tests/test_golden.py`: centroids,
inertia, silhouette, iterations and labels agree (floats to a relative 1e-12), and the 2,100-customer
case, which samples the silhouette, agrees on the digest of its assignments. The set covers
the hazards RN-58 names: ties between equidistant centroids, an empty cluster refilled,
identical customers, a tolerance equal to the last shift, the iteration limit, and
equal-ranked clusters (RN-38).

Mutating the port (a strict tolerance, a plain sum for `fsum`, the highest id for the lowest
as the tie-break) fails the fixtures; that is how the cases were chosen.

## Repeatability

The same data, configuration and seed give the same centroids, metrics, dataset fingerprint
and iteration count, and customers read in another order reach the same partition
(`test_training.py`). When no seed is requested the service draws one and records it, so a
run can always be repeated.
