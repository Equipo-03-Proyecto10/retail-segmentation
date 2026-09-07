# ADR-0015 — Docker Compose is a development path only, and is never run on the instance

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #109 (F6-07), #89 (F3-09)
**Supersedes:** [ADR-0006](0006-run-under-both-systemd-and-docker-compose.md)
**Superseded by:** —

---

## Context

[ADR-0006](0006-run-under-both-systemd-and-docker-compose.md) settled that the
application runs under both systemd and Docker Compose, with systemd the default
on the instance. That much still holds and is restated below unchanged.

One clause of it does not. ADR-0006 said Compose is "what the demonstration's
container item is shown with, **including on the instance when it is switched in
for that purpose**", and that the two paths "are alternatives and are never up at
once". Read together, those commit the team to stopping the process that serves
the delivery in order to demonstrate the other one.

Three things have changed since it was accepted, and each makes that worse:

- The instance is now published at a real hostname through Cloudflare
  ([ADR-0013](0013-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md)),
  so the URL under demonstration is the one a reviewer may be looking at.
- The firewall was narrowed to `tcp:22` and `tcp:443` from Cloudflare's ranges
  (#167). The instance has one way in and it goes through NGINX.
- `compose.yaml` binds `127.0.0.1:8000` for `web` — the address the systemd
  unit's gunicorn holds — and `127.0.0.1:5432` for `db`, where the instance's
  own PostgreSQL already listens. Neither can be brought up there without
  displacing something that is serving.

The second binding is the harder one, and it is worth stating plainly because it
is not a packaging detail. ADR-0006 assumed the same image could talk "to the
`db` service locally and to the instance's own PostgreSQL there". Nothing in
`compose.yaml` provides that: `web` depends on `db` with a health condition, and
a container's `127.0.0.1` is its own loopback rather than the host's. Making it
work needs a network mode and a `DATABASE_URL` that do not exist yet.

What is genuinely uncertain is whether a grader reads "ejecución mediante
contenedores" as *the delivered system runs in containers* or as *the team can
run this application in containers*. RNF-13 is the team's own statement of the
requirement and says the second: the application runs under both, and systemd is
the default on the instance. This record follows RNF-13. If the Product Owner
reads it the other way, this is the decision to revisit, and it is cheaper to
revisit a record than to discover it during the demonstration.

## Decision

The application runs under both systemd and Docker Compose, reading identical
configuration from the environment in either. **systemd is how the delivery is
deployed and served, and Docker Compose is a development convenience with no
role in deployment: it is never run on the instance.** The demonstration's
container item is shown on a developer machine, and the evidence for it is
captured there.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep ADR-0006 as written and run Compose on the instance for the demonstration | It requires stopping the gunicorn that serves the published URL, on the single environment the delivery is graded on, during the window when it is most likely to be looked at. The rollback is a second manual step with nothing supervising it |
| Add an instance-only Compose override with `network_mode: host` and a `DATABASE_URL` pointing at the host's PostgreSQL | Solves the port collisions, and buys a second deployment path that must be kept working, tested, and understood — to demonstrate something RNF-13 does not ask to be demonstrated there. It is new deployment surface in the last days of the delivery |
| Drop Docker Compose entirely and delete `compose.yaml` | The reason it exists is unaffected by any of this: a developer joining the team otherwise installs PostgreSQL by hand and runs three SQL scripts in the right order before anything works. That was ADR-0006's strongest argument and it still holds |
| Deploy the instance with Compose and retire systemd | Supersedes ADR-0001 and ADR-0011, rewrites F6-01, F6-02 and the F6-06 pipeline, and installs Docker on the instance, to change nothing a reviewer sees |

## Consequences

**What this makes easy.** The published URL is never deliberately interrupted.
`compose.yaml` stays a local file with local assumptions — loopback bindings, a
seeded `db` service, a known password — and none of them has to be reconciled
with the instance. The container demonstration is rehearsable on a laptop, as
often as needed, with nothing at stake.

**What this makes hard.** The container path is now exercised only where it is
convenient, which is the weaker half of ADR-0006's original honest expectation
and is now weaker still: nothing at all checks that the image runs on the
instance's OS. If containers ever do acquire a deployment role, that gap is
where the first surprise will come from. `web/requirements.txt` remains the only
thing keeping the two paths aligned.

**What must now be true elsewhere.** AC 4 of #109 asks for evidence of
`docker compose` serving the application; under this record that evidence is
captured on a developer machine, not on the instance, and the issue should say
so. The demonstration item in [`requirements.md`](../requirements.md) §4 is
satisfied the same way. [`evidence/f6-07-proof-of-deployment.md`](../evidence/f6-07-proof-of-deployment.md)
records the two port collisions and should point here once this is accepted.
Nothing in `deploy/` changes: it never referenced Compose.

## Compliance

The instance must have no container runtime and no Compose project running:

```bash
# Both must report nothing. A container runtime on the instance is a
# violation of this record, not a convenience.
gcloud compute ssh mosaiq-deployment-vm --zone northamerica-south1-a \
  --command 'command -v docker podman; systemctl is-active docker 2>/dev/null'

# The delivery is served by the systemd unit, not by a container.
gcloud compute ssh mosaiq-deployment-vm --zone northamerica-south1-a \
  --command 'systemctl is-active mosaiq && systemctl is-enabled mosaiq'
```

Locally, the container path must still work, which is what makes this a dual
path rather than a retirement:

```bash
docker compose up -d --build && curl -sf -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/login
docker compose down
```
