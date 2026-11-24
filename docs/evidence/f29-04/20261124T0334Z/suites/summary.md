# F29-04 the segmentation path, on one version

Started 2026-11-24T03:34:37Z, finished 2026-11-24T03:41:47Z.
21 checks, 21 held, 0 did not.

- **commit:** `fdf0caedb301d5308ea804ff9cb9bb5b43ce73cb`
- **describe:** `kit-py/v0.1.8-776-gfdf0caed`
- **environment:** `a clean database built by sql/00, 01 and 02`
- **drift study seconds:** `405`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| RFM | web against the hand values (F29-01: 14 customers worked on paper, the web's R, F, M and scores) | exit 0; every case run and passed, none skipped | exit 0, {'tests': 4, 'failures': 0, 'errors': 0, 'skipped': 0}, 0 s | [1](01-rfm-web.log) | ✓ |
| RFM | rfm-engine against the hand values (F29-01: the same fixture, byte for byte, through rfm-engine) | exit 0; every case run and passed, none skipped | exit 0, {'tests': 2, 'failures': 0, 'errors': 0, 'skipped': 0}, 1 s | [1](02-rfm-rfm-engine.log) | ✓ |
| Migración | a provoked change keeps the previous assignment (F22-15: a change closes the previous assignment and opens one) | exit 0; every case run and passed, none skipped | exit 0, {'tests': 25, 'failures': 0, 'errors': 0, 'skipped': 0}, 3 s | [1](03-migracion-rfm-engine.log) | ✓ |
| Migración | the matrix against a table worked on paper (F29-02: six customers moved by each cause, matrix and history) | exit 0; every case run and passed, none skipped | exit 0, {'tests': 7, 'failures': 0, 'errors': 0, 'skipped': 0}, 1 s | [1](04-migracion-segments.log) | ✓ |
| Incremental | incremental profiles against a full recomputation (F22-08: a recorded sale, the profile before, after and recomputed) | exit 0; every case run and passed, none skipped | exit 0, {'tests': 35, 'failures': 0, 'errors': 0, 'skipped': 0}, 5 s | [1](05-incremental-rfm-engine.log) | ✓ |
| K-means | the registry's fit pinned to the web's (F22-12: golden fixtures shared with the web) | exit 0; every case run and passed, none skipped | exit 0, {'tests': 35, 'failures': 0, 'errors': 0, 'skipped': 0}, 9 s | [1](06-k-means-model-registry.log) | ✓ |
| Drift | a planted shift through the running monitor (F23-09, F23-10: two passes, the alert and its review read back) | exit 0; every case run and passed, none skipped | exit 0, {'tests': 40, 'failures': 0, 'errors': 0, 'skipped': 0}, 3 s | [1](07-drift-model-registry.log) | ✓ |
| K-means | the registered configuration fitted 3 times from scratch | the same assignments, centroids and recorded metrics each time | the same each time | [1](kmeans-reproduction.txt) | ✓ |
| K-means | the groups are the registered ones (F23-04's partition digest) | 040e8e1a8171ac792d4e0c6235376b13a45602afa075f67d372a020e677d26e0 | 040e8e1a8171ac792d4e0c6235376b13a45602afa075f67d372a020e677d26e0 | [1](kmeans-reproduction.txt) | ✓ |
| Drift | the study's figures are F23-10's (SHA-256 of the figures) | cfb2ddcfacaa1cef431f6a2ae54294fa8138e44f73a31facc4d5be6d51630f25 | cfb2ddcfacaa1cef431f6a2ae54294fa8138e44f73a31facc4d5be6d51630f25 | [1](drift-figures.json) | ✓ |
| Drift | SPEND planted at 0.1: not detected, as F23-10 registers it | recorded, not a pass: the digest above pins the figures | 0 of 50 detected | [1](drift-figures.json) | ✓ |
| Drift | SPEND planted at 0.25: not detected, as F23-10 registers it | recorded, not a pass: the digest above pins the figures | 0 of 50 detected | [1](drift-figures.json) | ✓ |
| Drift | SPEND planted at 0.5: not detected, as F23-10 registers it | recorded, not a pass: the digest above pins the figures | 0 of 50 detected | [1](drift-figures.json) | ✓ |
| Drift | SPEND planted at 1: detected, with an alert on the planted variable and direction | 50 of 50 detected, and in each an alert on the planted variable | 50 detected, 50 with such an alert | [1](drift-figures.json) | ✓ |
| Drift | FREQUENCY planted at 0.1: not detected, as F23-10 registers it | recorded, not a pass: the digest above pins the figures | 0 of 50 detected | [1](drift-figures.json) | ✓ |
| Drift | FREQUENCY planted at 0.25: not detected, as F23-10 registers it | recorded, not a pass: the digest above pins the figures | 0 of 50 detected | [1](drift-figures.json) | ✓ |
| Drift | FREQUENCY planted at 0.4: not detected, as F23-10 registers it | recorded, not a pass: the digest above pins the figures | 0 of 50 detected | [1](drift-figures.json) | ✓ |
| Drift | FREQUENCY planted at 0.5: detected, with an alert on the planted variable and direction | 50 of 50 detected, and in each an alert on the planted variable | 50 detected, 50 with such an alert | [1](drift-figures.json) | ✓ |
| Drift | SPEND: at least one planted size is detected | at least 1 size detected | 1 of 4 sizes | [1](drift-figures.json) | ✓ |
| Drift | FREQUENCY: at least one planted size is detected | at least 1 size detected | 1 of 4 sizes | [1](drift-figures.json) | ✓ |
| Drift | no shift: alerts raised, and the 95% upper bound of the rate | within 1% | 0 of 200, bound 0.75% | [1](drift-figures.json) | ✓ |
