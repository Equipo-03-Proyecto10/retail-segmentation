# AN-02 — Proof of deployment for the second delivery

#411. Captured on 2026-10-16 between 00:42 and 00:45 UTC against the instance
and the published host `mosaiq.maxthecoder.online`, after release #506 put
`develop` at `53a364a` on `main` as `f6c7e82`.

No password, key or token appears below. The administrator's password was read
on the instance by the capture script's own process over SSH. It was never
printed, never written to a file, and never pasted anywhere. The script signed
out at the end, which ends the session.

## AC 1 — the release preserves ancestry, and the deploy succeeds

The analytics phase reached `main` in two releases on 2026-10-15, both merged
with a merge commit (ADR-0012):

- #489 → `c3f98e5`: the #353–#362 review fixes, ADR-0031 to ADR-0033 and #360.
  Tagged `delivery-2-hardening`.
- #506 → `f6c7e82`: everything merged since, including the distributed-stage
  foundation. Tagged `delivery-3-foundation`.

```
$ git log -1 --format='%h %p %s' origin/main
f6c7e82 c3f98e5 53a364a Merge pull request #506 from Equipo-03-Proyecto10/develop

$ git merge-base --is-ancestor origin/develop origin/main && echo ancestor
ancestor
```

`f6c7e82` has both parents: `main`'s previous head and `develop`'s head. Every
`develop` commit is therefore an ancestor of `main`, which is what ADR-0012
asks of a release. The deploy workflow run for `f6c7e82` (Actions run
38826685373) finished `success` with its one job, *Deploy to the instance*.

## AC 2 — the SQL was applied by hand, and the drift check finds nothing

`deploy.sh` never runs SQL, so both releases rebuilt the instance database by
hand before their merge, as `deploy/README.md` and
[`../runbook-instance-accounts.md`](../runbook-instance-accounts.md) describe:

1. `pg_dump` to `/var/tmp`.
2. Stop `mosaiq`, drop `retail`, and run `00`, `01` and `02` from the release
   commit.
3. Provision the administrator again, which deactivated the 30 demonstration
   accounts.
4. Run the verification block as `retail_app`: 16 PASS.
5. Run `contracts/db/check_grants.sh` and `contracts/db/check_boundaries.sql`:
   no output.

Release #506 also gave the sixteen service roles their own random passwords.
They were generated on the instance into a root-only file. No role accepts its
name as a password.

The drift check from #346 compares a clean build of `main`'s scripts with the
instance:

```
# reference: 00 and 01 from origin/main on a scratch PostgreSQL 18.6
$ PGDATABASE=retail deploy/check-schema-drift.sh dump reference

# live: on the instance, as the database owner
$ sudo -u postgres env PGDATABASE=retail deploy/check-schema-drift.sh dump /tmp/live

$ deploy/check-schema-drift.sh compare reference live
no drift: schema and retail_app privileges match
```

## AC 3 — what the instance serves

The commit served and the unit's state, on the instance:

```
$ sudo git -C /opt/mosaiq/current rev-parse HEAD
f6c7e82060cfd4572d7434b09ce5dc22afdc6601

$ systemctl status mosaiq --no-pager
● mosaiq.service - MOSAIQ web application (gunicorn)
     Loaded: loaded (/etc/systemd/system/mosaiq.service; enabled; preset: disabled)
     Active: active (running) since Fri 2026-10-16 00:42:02 UTC; 41s ago
 Invocation: 7879a149c65843769c8c90852d8ce2cd
       Docs: https://github.com/Equipo-03-Proyecto10/retail-segmentation/blob/develop/deploy/README.md
   Main PID: 1369 (gunicorn)
     Status: "Gunicorn arbiter booted"
      Tasks: 3 (limit: 48442)
     Memory: 86.8M (peak: 202.5M)
        CPU: 2.327s
     CGroup: /system.slice/mosaiq.service
             ├─1369 …
             ├─1606 …
             └─1613 …

$ systemctl is-enabled mosaiq; systemctl is-active mosaiq nginx postgresql-18
enabled
active active active

$ psql --version
psql (PostgreSQL) 18.6
```

`enabled` is what brings the unit back after a reboot. The process lines of the
control group are elided: they show only the PIDs and the loopback bind,
already recorded in [F6-07](f6-07-proof-of-deployment.md).

### The analytics pages through the published host

Signed in through the login form as the instance's administrator, at 1440 px,
full page. Every page answered 200. The account name in the navigation is
masked to *Administrator* before each capture. The role badge, `ADMIN`, is
left as captured.

| Page | Path | Status | Capture |
|---|---|---|---|
| Sales import | `/admin/sales-import/` | 200 | [an-02-sales-import-1440.png](an-02-sales-import-1440.png) |
| Segments | `/catalog/segments` | 200 | [an-02-segments-1440.png](an-02-segments-1440.png) |
| Campaigns | `/campaigns/` | 200 | [an-02-campaigns-1440.png](an-02-campaigns-1440.png) |
| Segment run | `/segment-run/` | 200 | [an-02-segment-run-1440.png](an-02-segment-run-1440.png) |
| Run history | `/run-history/` | 200 | [an-02-run-history-1440.png](an-02-run-history-1440.png) |
| Migration matrix | `/migration-matrix/` | 200 | [an-02-migration-matrix-1440.png](an-02-migration-matrix-1440.png) |
| Model comparison | `/model-comparison/` | 200 | [an-02-model-comparison-1440.png](an-02-model-comparison-1440.png) |
| Segmentation dashboard | `/segmentation-dashboard/` | 200 | [an-02-segmentation-dashboard-1440.png](an-02-segmentation-dashboard-1440.png) |
| Segment history report | `/segment-history-report/` | 200 | [an-02-segment-history-report-1440.png](an-02-segment-history-report-1440.png) |
| Consumption reports | `/consumption-reports/` | 200 | [an-02-consumption-reports-1440.png](an-02-consumption-reports-1440.png) |
| Experiment report | `/experiment-report/` | 200 | [an-02-experiment-report-1440.png](an-02-experiment-report-1440.png) |
| Reports | `/reports/` | 200 | [an-02-reports-1440.png](an-02-reports-1440.png) |

After signing out through the navigation's form, the same browser requesting
`/segmentation-dashboard/` was sent to `/login?next=/segmentation-dashboard/`,
so the session the captures used no longer exists.

The figures are the seed's demonstration data, as the dashboard's *Synthetic*
banner says. The instance database was rebuilt from the seed by release #506.
