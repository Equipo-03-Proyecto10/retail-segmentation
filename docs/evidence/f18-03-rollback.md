# F18-03 — deploying and rolling back one service at a time

Issue #456, [ADR-0040](../adr/0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md).
Recorded on 2026-10-23 on `mosaiq-deployment-vm`, with `deploy/deploy-service.sh`
and `deploy/rehearse-rollback.sh` from branch `feature/456-deploy-service`
(commit `0af1174`), copied to `/var/tmp/mosaiq-456/` on the instance and run
as root. The procedure is in
[`deploy/README.md`](../../deploy/README.md), F18-03.

## What this shows

| Criterion | Result |
|---|---|
| The script stages, loads the ACL line, pulls, starts, waits for readiness, installs the include and switches `current` | All 15 services deployed through it: every run exited 0, in about 2.2 s, and moved the service from F18-01's hand-staged `<name>/<version>` to `releases/<version>` |
| A failed step restores only this service's previous release, checks its health, and fails | 13 of 13 core services rehearsed. Each failed at step 5, exited 70, and served the previous release and tag again, ready |
| Every other project's containers are unchanged | Same container ids and start times before and after each rehearsal, for all 17 other containers |
| The web stays active | `mosaiq.service` stayed active and `https://127.0.0.1/` answered 200 after each rehearsal. A one-second monitor through Cloudflare saw 1201 samples, 0 not 200 or not active |
| One service's failed deploy does not hold back a concurrent deploy of another | `stores` deployed, exit 0, inside the 60 s readiness wait of `segments`' failing rehearsal |
| ADR-0040's compliance greps over the deploy path | Print nothing (below) |
| The workflow runs only after the release job, builds its matrix from the changed files and authenticates through Workload Identity Federation | Run [39992244364](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/39992244364), on the merge of release #710 to `main`: the matrix was segments and sales, whose `VERSION` the push changed. Each deploy ran after its release job, checked that the image was built from the merged commit, signed in through Workload Identity Federation, and deployed at the same moment as the other. Both succeeded |

## The workflow, end to end

Release #710 merged `develop` into `main` at `fbcd370` on 2026-10-23. It
carried two `VERSION` bumps, with no application change in either:

- segments 0.1.1 → 0.1.2, made for this run;
- sales 0.1.2 → 0.1.3, a test-only fix (#711).

`services.yml` run
[39992244364](https://github.com/Equipo-03-Proyecto10/retail-segmentation/actions/runs/39992244364)
went like this:

| Job | Result |
|---|---|
| `release (segments)`, `release (sales)` | built each image once, ran the conformance suite on it, and pushed `:0.1.2` and `:0.1.3` |
| `deploy / changes` | matrix `["sales","segments"]`, from the changed `VERSION` files |
| `deploy / deploy (segments)` | 17:08:22–17:09:39 UTC; revision label equal to `fbcd370`; script exit 0 |
| `deploy / deploy (sales)` | 17:08:22–17:09:43 UTC; revision label equal to `fbcd370`; script exit 0 |

The web's own `Deploy` run on the same push succeeded too, unchanged.

The script's output in `deploy (segments)`, layer lines left out:

```text
=== 1. segments 0.1.2: preconditions
serving: releases/0.1.1 (image tag 0.1.1)
```

And in `deploy (sales)`:

```text
=== 1. sales 0.1.3: preconditions
serving: releases/0.1.2 (image tag 0.1.2)
```

Afterwards, on the instance:

```text
segments -> releases/0.1.2 image ghcr.io/equipo-03-proyecto10/mosaiq-segments:0.1.2 ready 204
sales -> releases/0.1.3 image ghcr.io/equipo-03-proyecto10/mosaiq-sales:0.1.3 ready 204
mosaiq, mosaiq-svc@segments, mosaiq-svc@sales: active
web https 200
edge /api/segments/health/ready 204
edge /api/sales/health/ready 204
check-services: 0 lines
```

`docker ps` showed the two recreated containers at 13 minutes. The other 16
containers kept the start times they had before the release.

## Moving each service onto the script

Each service was deployed at the version it was already running, from the
branch's tree. That exercised every step on the real instance without changing
an image:

```sh
sudo bash /var/tmp/mosaiq-456/tree/deploy/deploy-service.sh <name> "$(cat services/<name>/VERSION)"
```

| Service | Version | Seconds | Exit |
|---|---|---|---|
| audit-trail | 0.1.2 | 2.2 | 0 |
| auth | 0.1.3 | 2.2 | 0 |
| campaigns | 0.1.2 | 2.2 | 0 |
| categories | 0.1.5 | 2.2 | 0 |
| check-ins | 0.1.3 | 2.2 | 0 |
| customer-preferences | 0.1.3 | 2.1 | 0 |
| customers | 0.1.6 | 2.3 | 0 |
| engagement | 0.1.1 | 2.2 | 0 |
| products | 0.1.4 | 2.2 | 0 |
| recommendations | 0.1.2 | 2.3 | 0 |
| sales | 0.1.2 | 2.2 | 0 |
| segments | 0.1.1 | 2.4 | 0 |
| stock | 0.1.2 | 2.1 | 0 |
| stores | 0.1.4 | 2.2 | 0 |
| users | 0.1.6 | 2.2 | 0 |

Compose found every container up to date (`Container … Running`), so no
container was recreated: `docker ps` taken before and after the batch of
thirteen shows the same 18 container ids. `segments` ran first, alone, with an
earlier draft of the script whose only difference is the wording of three
progress lines. Its output, as one example:

```text
=== 1. segments 0.1.1: preconditions
serving: 0.1.1 (image tag 0.1.1)

=== 2. Staging /opt/mosaiq/services/segments/releases/0.1.1

=== 3. Loading segments's ACL line into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.1 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-segments:0.1.1 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-segments:0.1.1 Pulled 
 Container mosaiq-segments-segments-1 Running 

=== 5. Waiting up to 60 s for http://127.0.0.1:5010/health/ready
ready

=== 6. Installing /etc/nginx/mosaiq-api.d/segments.conf
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful

=== 7. Pointing current at releases/0.1.1

=== Deployed segments 0.1.1
serving: releases/0.1.1 (image tag 0.1.1); previous: 0.1.1
```

## The rollback rehearsals

`rehearse-rollback.sh <name>` stages `<tag>+rehearsal`, a release of the tag
already serving whose compose file publishes it on port + 10000. Its
`/health/ready` on the registered port therefore cannot pass. The script deploys
it with `deploy-service.sh` and checks the outcome. Build metadata names a
separate release of the same image, so no new image was needed.

| Service | Seconds | Checks |
|---|---|---|
| segments | 85 | 7 of 7 |
| auth | 74 | 7 of 7 |
| users | 75 | 7 of 7 |
| products | 74 | 7 of 7 |
| categories | 75 | 7 of 7 |
| stores | 85 | 7 of 7 |
| stock | 85 | 7 of 7 |
| customers | 75 | 7 of 7 |
| customer-preferences | 85 | 7 of 7 |
| sales | 75 | 7 of 7 |
| recommendations | 76 | 7 of 7 |
| engagement | 75 | 7 of 7 |
| check-ins | 85 | 7 of 7 |

91 checks held and 0 failed. `segments` was rehearsed concurrently with the
`stores` deploy (next section). Each service's full record follows, as the
script printed it.

## A concurrent deploy completes while another fails

```text
2026-10-23T13:49:41.690Z start rehearse segments
2026-10-23T13:49:49.692Z start migrate stores 0.1.4
2026-10-23T13:49:51.893Z end migrate stores 0.1.4 exit 0
2026-10-23T13:51:06.599Z end rehearse segments exit 0
2026-10-23T13:56:46.795Z start rehearse stores
2026-10-23T13:58:11.451Z end rehearse stores exit 0
```

`stores`' deploy started 8 s into the `segments` rehearsal and finished, exit 0,
during the 60 s readiness wait. It took the host lock for steps 3 and 6 while the
failing deploy held none. The `segments` rollback then restored its own ACL line
and kept the one `stores` had just recorded.

## ADR-0040's compliance greps

Run from the repository root at `0af1174`. All print nothing:

```sh
grep -nE 'docker compose' .github/workflows/deploy-service.yml deploy/deploy-service.sh | grep -v -- '-p mosaiq-'
grep -nE 'docker (compose .*)?build|--build' deploy/deploy-service.sh .github/workflows/deploy-service.yml
grep -rnE 'mosaiq-(auth|users|products|categories|stores|stock|customers|customer-preferences|sales|segments|recommendations|engagement|check-ins|campaigns|audit-trail)\b' .github/ deploy/ --exclude=check-services.sh
grep -nE 'docker|ghcr|services/' deploy/deploy.sh .github/workflows/deploy.yml
```

`.github/scripts/guards.sh` now runs the first, second and fourth on every pull
request, and refuses `psql`, `pg_dump` and `pg_restore` in the service deploy.

## Records per service

### segments, image tag 0.1.1

Rehearsed at 2026-10-23T13:49:41Z with `0.1.1+rehearsal`, published on 127.0.0.1:15010 instead of 5010.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.1`: `releases/0.1.1`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-segments:0.1.1`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-segments:0.1.1`
- [x] `/health/ready` on 5010 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. segments 0.1.1+rehearsal: preconditions
serving: releases/0.1.1 (image tag 0.1.1)

=== 2. Staging /opt/mosaiq/services/segments/releases/0.1.1+rehearsal

=== 3. Loading the ACL line of segments into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.1 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-segments:0.1.1 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-segments:0.1.1 Pulled 
 Container mosaiq-segments-segments-1 Recreate 
 Container mosaiq-segments-segments-1 Recreated 
 Container mosaiq-segments-segments-1 Starting 
 Container mosaiq-segments-segments-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5010/health/ready
deploy-service: segments is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5010 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of segments
 Container mosaiq-segments-segments-1 Recreate 
 Container mosaiq-segments-segments-1 Recreated 
 Container mosaiq-segments-segments-1 Starting 
 Container mosaiq-segments-segments-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.1 (image tag 0.1.1) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 7 hours ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 12 hours ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 7 hours ago
mosaiq-customers-customers-1 7 hours ago
mosaiq-engagement-engagement-1 12 hours ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 12 hours ago
mosaiq-recommendations-recommendations-1 7 hours ago
mosaiq-sales-sales-1 7 hours ago
mosaiq-segments-segments-1 11 seconds ago
mosaiq-stock-stock-1 10 hours ago
mosaiq-stores-stores-1 12 hours ago
mosaiq-users-users-1 7 hours ago
```

### auth, image tag 0.1.3

Rehearsed at 2026-10-23T13:51:48Z with `0.1.3+rehearsal`, published on 127.0.0.1:15001 instead of 5001.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.3`: `releases/0.1.3`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-auth:0.1.3`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-auth:0.1.3`
- [x] `/health/ready` on 5001 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. auth 0.1.3+rehearsal: preconditions
serving: releases/0.1.3 (image tag 0.1.3)

=== 2. Staging /opt/mosaiq/services/auth/releases/0.1.3+rehearsal

=== 3. Loading the ACL line of auth into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.3 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-auth:0.1.3 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-auth:0.1.3 Pulled 
 Container mosaiq-auth-auth-1 Recreate 
 Container mosaiq-auth-auth-1 Recreated 
 Container mosaiq-auth-auth-1 Starting 
 Container mosaiq-auth-auth-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5001/health/ready
deploy-service: auth is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5001 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of auth
 Container mosaiq-auth-auth-1 Recreate 
 Container mosaiq-auth-auth-1 Recreated 
 Container mosaiq-auth-auth-1 Starting 
 Container mosaiq-auth-auth-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.3 (image tag 0.1.3) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 9 seconds ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 12 hours ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 7 hours ago
mosaiq-customers-customers-1 7 hours ago
mosaiq-engagement-engagement-1 12 hours ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 12 hours ago
mosaiq-recommendations-recommendations-1 7 hours ago
mosaiq-sales-sales-1 7 hours ago
mosaiq-segments-segments-1 2 minutes ago
mosaiq-stock-stock-1 10 hours ago
mosaiq-stores-stores-1 12 hours ago
mosaiq-users-users-1 7 hours ago
```

### users, image tag 0.1.6

Rehearsed at 2026-10-23T13:53:01Z with `0.1.6+rehearsal`, published on 127.0.0.1:15002 instead of 5002.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.6`: `releases/0.1.6`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-users:0.1.6`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-users:0.1.6`
- [x] `/health/ready` on 5002 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. users 0.1.6+rehearsal: preconditions
serving: releases/0.1.6 (image tag 0.1.6)

=== 2. Staging /opt/mosaiq/services/users/releases/0.1.6+rehearsal

=== 3. Loading the ACL line of users into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.6 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-users:0.1.6 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-users:0.1.6 Pulled 
 Container mosaiq-users-users-1 Recreate 
 Container mosaiq-users-users-1 Recreated 
 Container mosaiq-users-users-1 Starting 
 Container mosaiq-users-users-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5002/health/ready
deploy-service: users is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5002 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of users
 Container mosaiq-users-users-1 Recreate 
 Container mosaiq-users-users-1 Recreated 
 Container mosaiq-users-users-1 Starting 
 Container mosaiq-users-users-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.6 (image tag 0.1.6) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 About a minute ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 12 hours ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 7 hours ago
mosaiq-customers-customers-1 7 hours ago
mosaiq-engagement-engagement-1 12 hours ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 13 hours ago
mosaiq-recommendations-recommendations-1 7 hours ago
mosaiq-sales-sales-1 7 hours ago
mosaiq-segments-segments-1 3 minutes ago
mosaiq-stock-stock-1 10 hours ago
mosaiq-stores-stores-1 12 hours ago
mosaiq-users-users-1 10 seconds ago
```

### products, image tag 0.1.4

Rehearsed at 2026-10-23T13:54:17Z with `0.1.4+rehearsal`, published on 127.0.0.1:15003 instead of 5003.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.4`: `releases/0.1.4`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-products:0.1.4`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-products:0.1.4`
- [x] `/health/ready` on 5003 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. products 0.1.4+rehearsal: preconditions
serving: releases/0.1.4 (image tag 0.1.4)

=== 2. Staging /opt/mosaiq/services/products/releases/0.1.4+rehearsal

=== 3. Loading the ACL line of products into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.4 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-products:0.1.4 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-products:0.1.4 Pulled 
 Container mosaiq-products-products-1 Recreate 
 Container mosaiq-products-products-1 Recreated 
 Container mosaiq-products-products-1 Starting 
 Container mosaiq-products-products-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5003/health/ready
deploy-service: products is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5003 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of products
 Container mosaiq-products-products-1 Recreate 
 Container mosaiq-products-products-1 Recreated 
 Container mosaiq-products-products-1 Starting 
 Container mosaiq-products-products-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.4 (image tag 0.1.4) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 2 minutes ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 13 hours ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 7 hours ago
mosaiq-customers-customers-1 7 hours ago
mosaiq-engagement-engagement-1 12 hours ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 10 seconds ago
mosaiq-recommendations-recommendations-1 7 hours ago
mosaiq-sales-sales-1 7 hours ago
mosaiq-segments-segments-1 4 minutes ago
mosaiq-stock-stock-1 10 hours ago
mosaiq-stores-stores-1 12 hours ago
mosaiq-users-users-1 About a minute ago
```

### categories, image tag 0.1.5

Rehearsed at 2026-10-23T13:55:31Z with `0.1.5+rehearsal`, published on 127.0.0.1:15004 instead of 5004.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.5`: `releases/0.1.5`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-categories:0.1.5`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-categories:0.1.5`
- [x] `/health/ready` on 5004 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. categories 0.1.5+rehearsal: preconditions
serving: releases/0.1.5 (image tag 0.1.5)

=== 2. Staging /opt/mosaiq/services/categories/releases/0.1.5+rehearsal

=== 3. Loading the ACL line of categories into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.5 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-categories:0.1.5 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-categories:0.1.5 Pulled 
 Container mosaiq-categories-categories-1 Recreate 
 Container mosaiq-categories-categories-1 Recreated 
 Container mosaiq-categories-categories-1 Starting 
 Container mosaiq-categories-categories-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5004/health/ready
deploy-service: categories is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5004 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of categories
 Container mosaiq-categories-categories-1 Recreate 
 Container mosaiq-categories-categories-1 Recreated 
 Container mosaiq-categories-categories-1 Starting 
 Container mosaiq-categories-categories-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.5 (image tag 0.1.5) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 3 minutes ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 10 seconds ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 7 hours ago
mosaiq-customers-customers-1 7 hours ago
mosaiq-engagement-engagement-1 12 hours ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 About a minute ago
mosaiq-recommendations-recommendations-1 7 hours ago
mosaiq-sales-sales-1 7 hours ago
mosaiq-segments-segments-1 5 minutes ago
mosaiq-stock-stock-1 10 hours ago
mosaiq-stores-stores-1 12 hours ago
mosaiq-users-users-1 2 minutes ago
```

### stores, image tag 0.1.4

Rehearsed at 2026-10-23T13:56:46Z with `0.1.4+rehearsal`, published on 127.0.0.1:15005 instead of 5005.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.4`: `releases/0.1.4`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-stores:0.1.4`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-stores:0.1.4`
- [x] `/health/ready` on 5005 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. stores 0.1.4+rehearsal: preconditions
serving: releases/0.1.4 (image tag 0.1.4)

=== 2. Staging /opt/mosaiq/services/stores/releases/0.1.4+rehearsal

=== 3. Loading the ACL line of stores into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.4 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-stores:0.1.4 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-stores:0.1.4 Pulled 
 Container mosaiq-stores-stores-1 Recreate 
 Container mosaiq-stores-stores-1 Recreated 
 Container mosaiq-stores-stores-1 Starting 
 Container mosaiq-stores-stores-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5005/health/ready
deploy-service: stores is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5005 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of stores
 Container mosaiq-stores-stores-1 Recreate 
 Container mosaiq-stores-stores-1 Recreated 
 Container mosaiq-stores-stores-1 Starting 
 Container mosaiq-stores-stores-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.4 (image tag 0.1.4) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 5 minutes ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 About a minute ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 7 hours ago
mosaiq-customers-customers-1 7 hours ago
mosaiq-engagement-engagement-1 12 hours ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 2 minutes ago
mosaiq-recommendations-recommendations-1 7 hours ago
mosaiq-sales-sales-1 7 hours ago
mosaiq-segments-segments-1 7 minutes ago
mosaiq-stock-stock-1 11 hours ago
mosaiq-stores-stores-1 12 seconds ago
mosaiq-users-users-1 4 minutes ago
```

### stock, image tag 0.1.2

Rehearsed at 2026-10-23T13:58:11Z with `0.1.2+rehearsal`, published on 127.0.0.1:15006 instead of 5006.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.2`: `releases/0.1.2`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-stock:0.1.2`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-stock:0.1.2`
- [x] `/health/ready` on 5006 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. stock 0.1.2+rehearsal: preconditions
serving: releases/0.1.2 (image tag 0.1.2)

=== 2. Staging /opt/mosaiq/services/stock/releases/0.1.2+rehearsal

=== 3. Loading the ACL line of stock into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.2 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-stock:0.1.2 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-stock:0.1.2 Pulled 
 Container mosaiq-stock-stock-1 Recreate 
 Container mosaiq-stock-stock-1 Recreated 
 Container mosaiq-stock-stock-1 Starting 
 Container mosaiq-stock-stock-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5006/health/ready
deploy-service: stock is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5006 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of stock
 Container mosaiq-stock-stock-1 Recreate 
 Container mosaiq-stock-stock-1 Recreated 
 Container mosaiq-stock-stock-1 Starting 
 Container mosaiq-stock-stock-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.2 (image tag 0.1.2) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 6 minutes ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 3 minutes ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 7 hours ago
mosaiq-customers-customers-1 7 hours ago
mosaiq-engagement-engagement-1 12 hours ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 4 minutes ago
mosaiq-recommendations-recommendations-1 7 hours ago
mosaiq-sales-sales-1 7 hours ago
mosaiq-segments-segments-1 8 minutes ago
mosaiq-stock-stock-1 12 seconds ago
mosaiq-stores-stores-1 About a minute ago
mosaiq-users-users-1 5 minutes ago
```

### customers, image tag 0.1.6

Rehearsed at 2026-10-23T13:59:36Z with `0.1.6+rehearsal`, published on 127.0.0.1:15007 instead of 5007.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.6`: `releases/0.1.6`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-customers:0.1.6`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-customers:0.1.6`
- [x] `/health/ready` on 5007 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. customers 0.1.6+rehearsal: preconditions
serving: releases/0.1.6 (image tag 0.1.6)

=== 2. Staging /opt/mosaiq/services/customers/releases/0.1.6+rehearsal

=== 3. Loading the ACL line of customers into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.6 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-customers:0.1.6 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-customers:0.1.6 Pulled 
 Container mosaiq-customers-customers-1 Recreate 
 Container mosaiq-customers-customers-1 Recreated 
 Container mosaiq-customers-customers-1 Starting 
 Container mosaiq-customers-customers-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5007/health/ready
deploy-service: customers is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5007 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of customers
 Container mosaiq-customers-customers-1 Recreate 
 Container mosaiq-customers-customers-1 Recreated 
 Container mosaiq-customers-customers-1 Starting 
 Container mosaiq-customers-customers-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.6 (image tag 0.1.6) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 7 minutes ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 4 minutes ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 7 hours ago
mosaiq-customers-customers-1 9 seconds ago
mosaiq-engagement-engagement-1 12 hours ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 5 minutes ago
mosaiq-recommendations-recommendations-1 7 hours ago
mosaiq-sales-sales-1 7 hours ago
mosaiq-segments-segments-1 9 minutes ago
mosaiq-stock-stock-1 About a minute ago
mosaiq-stores-stores-1 2 minutes ago
mosaiq-users-users-1 6 minutes ago
```

### customer-preferences, image tag 0.1.3

Rehearsed at 2026-10-23T14:00:51Z with `0.1.3+rehearsal`, published on 127.0.0.1:15008 instead of 5008.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.3`: `releases/0.1.3`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-customer-preferences:0.1.3`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-customer-preferences:0.1.3`
- [x] `/health/ready` on 5008 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. customer-preferences 0.1.3+rehearsal: preconditions
serving: releases/0.1.3 (image tag 0.1.3)

=== 2. Staging /opt/mosaiq/services/customer-preferences/releases/0.1.3+rehearsal

=== 3. Loading the ACL line of customer-preferences into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.3 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-customer-preferences:0.1.3 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-customer-preferences:0.1.3 Pulled 
 Container mosaiq-customer-preferences-customer-preferences-1 Recreate 
 Container mosaiq-customer-preferences-customer-preferences-1 Recreated 
 Container mosaiq-customer-preferences-customer-preferences-1 Starting 
 Container mosaiq-customer-preferences-customer-preferences-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5008/health/ready
deploy-service: customer-preferences is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5008 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of customer-preferences
 Container mosaiq-customer-preferences-customer-preferences-1 Recreate 
 Container mosaiq-customer-preferences-customer-preferences-1 Recreated 
 Container mosaiq-customer-preferences-customer-preferences-1 Starting 
 Container mosaiq-customer-preferences-customer-preferences-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.3 (image tag 0.1.3) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 9 minutes ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 5 minutes ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 11 seconds ago
mosaiq-customers-customers-1 About a minute ago
mosaiq-engagement-engagement-1 12 hours ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 6 minutes ago
mosaiq-recommendations-recommendations-1 7 hours ago
mosaiq-sales-sales-1 7 hours ago
mosaiq-segments-segments-1 11 minutes ago
mosaiq-stock-stock-1 2 minutes ago
mosaiq-stores-stores-1 4 minutes ago
mosaiq-users-users-1 8 minutes ago
```

### sales, image tag 0.1.2

Rehearsed at 2026-10-23T14:02:15Z with `0.1.2+rehearsal`, published on 127.0.0.1:15009 instead of 5009.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.2`: `releases/0.1.2`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-sales:0.1.2`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-sales:0.1.2`
- [x] `/health/ready` on 5009 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. sales 0.1.2+rehearsal: preconditions
serving: releases/0.1.2 (image tag 0.1.2)

=== 2. Staging /opt/mosaiq/services/sales/releases/0.1.2+rehearsal

=== 3. Loading the ACL line of sales into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.2 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-sales:0.1.2 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-sales:0.1.2 Pulled 
 Container mosaiq-sales-sales-1 Recreate 
 Container mosaiq-sales-sales-1 Recreated 
 Container mosaiq-sales-sales-1 Starting 
 Container mosaiq-sales-sales-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5009/health/ready
deploy-service: sales is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5009 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of sales
 Container mosaiq-sales-sales-1 Recreate 
 Container mosaiq-sales-sales-1 Recreated 
 Container mosaiq-sales-sales-1 Starting 
 Container mosaiq-sales-sales-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.2 (image tag 0.1.2) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 10 minutes ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 6 minutes ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 About a minute ago
mosaiq-customers-customers-1 2 minutes ago
mosaiq-engagement-engagement-1 12 hours ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 8 minutes ago
mosaiq-recommendations-recommendations-1 7 hours ago
mosaiq-sales-sales-1 10 seconds ago
mosaiq-segments-segments-1 12 minutes ago
mosaiq-stock-stock-1 4 minutes ago
mosaiq-stores-stores-1 5 minutes ago
mosaiq-users-users-1 9 minutes ago
```

### recommendations, image tag 0.1.2

Rehearsed at 2026-10-23T14:03:30Z with `0.1.2+rehearsal`, published on 127.0.0.1:15011 instead of 5011.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.2`: `releases/0.1.2`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-recommendations:0.1.2`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-recommendations:0.1.2`
- [x] `/health/ready` on 5011 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. recommendations 0.1.2+rehearsal: preconditions
serving: releases/0.1.2 (image tag 0.1.2)

=== 2. Staging /opt/mosaiq/services/recommendations/releases/0.1.2+rehearsal

=== 3. Loading the ACL line of recommendations into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.2 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-recommendations:0.1.2 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-recommendations:0.1.2 Pulled 
 Container mosaiq-recommendations-recommendations-1 Recreate 
 Container mosaiq-recommendations-recommendations-1 Recreated 
 Container mosaiq-recommendations-recommendations-1 Starting 
 Container mosaiq-recommendations-recommendations-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5011/health/ready
deploy-service: recommendations is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5011 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of recommendations
 Container mosaiq-recommendations-recommendations-1 Recreate 
 Container mosaiq-recommendations-recommendations-1 Recreated 
 Container mosaiq-recommendations-recommendations-1 Starting 
 Container mosaiq-recommendations-recommendations-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.2 (image tag 0.1.2) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 11 minutes ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 8 minutes ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 2 minutes ago
mosaiq-customers-customers-1 4 minutes ago
mosaiq-engagement-engagement-1 12 hours ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 9 minutes ago
mosaiq-recommendations-recommendations-1 11 seconds ago
mosaiq-sales-sales-1 About a minute ago
mosaiq-segments-segments-1 13 minutes ago
mosaiq-stock-stock-1 5 minutes ago
mosaiq-stores-stores-1 6 minutes ago
mosaiq-users-users-1 10 minutes ago
```

### engagement, image tag 0.1.1

Rehearsed at 2026-10-23T14:04:46Z with `0.1.1+rehearsal`, published on 127.0.0.1:15012 instead of 5012.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.1`: `releases/0.1.1`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-engagement:0.1.1`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-engagement:0.1.1`
- [x] `/health/ready` on 5012 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. engagement 0.1.1+rehearsal: preconditions
serving: releases/0.1.1 (image tag 0.1.1)

=== 2. Staging /opt/mosaiq/services/engagement/releases/0.1.1+rehearsal

=== 3. Loading the ACL line of engagement into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.1 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-engagement:0.1.1 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-engagement:0.1.1 Pulled 
 Container mosaiq-engagement-engagement-1 Recreate 
 Container mosaiq-engagement-engagement-1 Recreated 
 Container mosaiq-engagement-engagement-1 Starting 
 Container mosaiq-engagement-engagement-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5012/health/ready
deploy-service: engagement is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5012 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of engagement
 Container mosaiq-engagement-engagement-1 Recreate 
 Container mosaiq-engagement-engagement-1 Recreated 
 Container mosaiq-engagement-engagement-1 Starting 
 Container mosaiq-engagement-engagement-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.1 (image tag 0.1.1) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 13 minutes ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 9 minutes ago
mosaiq-check-ins-check-ins-1 12 hours ago
mosaiq-customer-preferences-customer-preferences-1 3 minutes ago
mosaiq-customers-customers-1 5 minutes ago
mosaiq-engagement-engagement-1 10 seconds ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 10 minutes ago
mosaiq-recommendations-recommendations-1 About a minute ago
mosaiq-sales-sales-1 2 minutes ago
mosaiq-segments-segments-1 15 minutes ago
mosaiq-stock-stock-1 6 minutes ago
mosaiq-stores-stores-1 8 minutes ago
mosaiq-users-users-1 11 minutes ago
```

### check-ins, image tag 0.1.3

Rehearsed at 2026-10-23T14:06:01Z with `0.1.3+rehearsal`, published on 127.0.0.1:15013 instead of 5013.

- [x] deploy-service.sh exited 70: 70 is a completed rollback
- [x] it failed at step 5, readiness
- [x] current is still `releases/0.1.3`: `releases/0.1.3`
- [x] the image serving before, `ghcr.io/equipo-03-proyecto10/mosaiq-check-ins:0.1.3`, serves again: `ghcr.io/equipo-03-proyecto10/mosaiq-check-ins:0.1.3`
- [x] `/health/ready` on 5013 answers 204
- [x] every other project's 17 containers are the same containers, started at the same moment
- [x] mosaiq.service is active, and https://127.0.0.1/ answers 200

<details><summary>deploy-service.sh output</summary>

```text

=== 1. check-ins 0.1.3+rehearsal: preconditions
serving: releases/0.1.3 (image tag 0.1.3)

=== 2. Staging /opt/mosaiq/services/check-ins/releases/0.1.3+rehearsal

=== 3. Loading the ACL line of check-ins into redis-state and redis-cache
loaded 15 service users into redis-state and redis-cache

=== 4. Pulling image tag 0.1.3 and starting it
 Image ghcr.io/equipo-03-proyecto10/mosaiq-check-ins:0.1.3 Pulling 
 Image ghcr.io/equipo-03-proyecto10/mosaiq-check-ins:0.1.3 Pulled 
 Container mosaiq-check-ins-check-ins-1 Recreate 
 Container mosaiq-check-ins-check-ins-1 Recreated 
 Container mosaiq-check-ins-check-ins-1 Starting 
 Container mosaiq-check-ins-check-ins-1 Started 

=== 5. Waiting up to 60 s for http://127.0.0.1:5013/health/ready
deploy-service: check-ins is not ready after 60 s: curl: (7) Failed to connect to 127.0.0.1 port 5013 after 0 ms: Could not connect to server
 (HTTP 000)
deploy-service: step 5 failed: the release never became ready

=== FAILED at step 5: restoring the previous release of check-ins
 Container mosaiq-check-ins-check-ins-1 Recreate 
 Container mosaiq-check-ins-check-ins-1 Recreated 
 Container mosaiq-check-ins-check-ins-1 Starting 
 Container mosaiq-check-ins-check-ins-1 Started 
loaded 15 service users into redis-state and redis-cache
rolled back: releases/0.1.3 (image tag 0.1.3) is serving again
```

</details>

`docker ps --format '{{.Names}} {{.RunningFor}}'` after the rehearsal:

```text
mosaiq-audit-trail-audit-trail-1 12 hours ago
mosaiq-auth-auth-1 14 minutes ago
mosaiq-campaigns-campaigns-1 12 hours ago
mosaiq-categories-categories-1 10 minutes ago
mosaiq-check-ins-check-ins-1 12 seconds ago
mosaiq-customer-preferences-customer-preferences-1 5 minutes ago
mosaiq-customers-customers-1 6 minutes ago
mosaiq-engagement-engagement-1 About a minute ago
mosaiq-infra-mongodb-1 12 hours ago
mosaiq-infra-redis-cache-1 12 hours ago
mosaiq-infra-redis-state-1 12 hours ago
mosaiq-products-products-1 12 minutes ago
mosaiq-recommendations-recommendations-1 2 minutes ago
mosaiq-sales-sales-1 4 minutes ago
mosaiq-segments-segments-1 16 minutes ago
mosaiq-stock-stock-1 8 minutes ago
mosaiq-stores-stores-1 9 minutes ago
mosaiq-users-users-1 13 minutes ago
```

