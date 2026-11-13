# ADR-0080 — The nightly chain and the emulator pull service images keyed by their build context, and build only what is missing

**Status:** Accepted (2026-11-12)
**Owner:** Marcelo
**Issue:** #979
**Supersedes:** in part [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md) (only its rule that CI pushes an image only for a merged `VERSION` bump; every release package keeps that rule)
**Superseded by:** —

---

## Context

- **Rebuilding dominates these two jobs.** `nightly.yml` and the emulator job in `android.yml` run `compose up --build` against `develop` every day. Over 5 to 9 November 2026, building the fifteen images from source took 523 to 709 s of each nightly run and 515 to 595 s of each emulator run. The e2e and parity tests take about 10 s, and the instrumented tests about 2.5 minutes.
- **Rust builds are the bulk of it.** Six Rust release builds run at once on a 4-vCPU runner, each taking 460 to 636 s.
- **Every build is a chance to stall.** On 6 November, 3 of 24 Rust builds stalled 9 to 11.5 minutes on a single `apt` download.
- **Most images don't change between runs.** `develop` changes a few services a day.
- **No image of `develop` exists to reuse.** ADR-0040 keeps the instance honest: a pull request pushes nothing, and only a merged `VERSION` bump pushes, as `:<VERSION>`, never overwriting a tag.
- **That is what makes a content key possible.** ADR-0045 limits a service's build context to its own directory, plus the contracts that `.github/scripts/stage-contracts.sh` stages into it (`contracts/<name>/` and `contracts/common/v1/`). Dependencies, the shared kit included, are pinned in the lock file inside that directory, and base images are pinned by digest. So an image is determined by three git trees.
- **A cache is a weak option on the current plan.** The Actions cache held 7.2 GB of its 10 GB on 9 November and 4.6 GB on 12 November. Its size swings with each week's runs, and going past 10 GB needs a paid plan.
- **Two things are uncertain.**
  - Pull time is an estimate, about a minute for fifteen images; it hasn't been measured.
  - GitHub's billing page says container storage is free today, private images included, and promises at least a month's notice before that changes.

## Decision

The nightly chain (`nightly.yml`) and the emulator job (`android.yml`) take each service's image from GHCR when one exists for the exact content under test, and build only the rest. An image is keyed by the git tree hashes of `services/<name>/`, `contracts/<name>/` and `contracts/common/v1/`, the three trees `stage-contracts.sh` puts into a build context. It is stored as `ghcr.io/equipo-03-proyecto10/mosaiq-ci-<name>:<key>`, a package separate from the release package `mosaiq-<name>`. A run pulls every image whose key exists, builds the missing ones from source, and pushes what it built, but only when it runs from `refs/heads/develop`. A key names exactly one content, so a tag is never overwritten. Pull requests never push a `mosaiq-ci-*` image, and nothing that deploys reads one. The documented `compose up --build` keeps building every image from source in Service template on a push, and in `ops/verify/run.sh`. For every release package, ADR-0040's rule stands: only a merged `VERSION` bump pushes, as `:<VERSION>`.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep building from source on every run | 523 to 709 s of each nightly run and 515 to 595 s of each emulator run go to rebuilding images that mostly did not change. Every build is another chance at the 9 to 11.5 minute package stall seen on 6 November |
| A BuildKit layer cache in the Actions cache (`type=gha`), with a dependency layer in the Rust Dockerfile template | It needs no record and would also speed up pull request builds. But the builder layers of fifteen images would share the 10 GB Actions cache with everything already in it (4.6 to 7.2 GB that week), and going past 10 GB needs a paid plan, so evictions would make the saving unpredictable. Changing the six Rust Dockerfiles also forces six `VERSION` bumps and six deploys. It can still be done on its own; it doesn't replace this |
| Pull the release images (`:<VERSION>`) | They are built from `main`. The nightly chain tests `develop`, so every service changed since its last release would run at its old version |
| A moving `:develop` tag per service | It overwrites a tag, which ADR-0040 never does, and a nightly run could pull an image newer or older than the commit it checked out |
| Push from the service job in `services.yml` after each merge to `develop` | That job also runs pull request code, and the `packages: write` it would need also covers the release packages. A separate push job would build each image a second time |

## Consequences

**What this makes easy.**
- Nightly and emulator runs reach their tests in about a minute plus rebuilds of the services that changed since the last run, instead of 9 to 12 minutes. That is an estimate, to be confirmed by the first runs.
- Runner time drops by roughly 15 minutes a day.
- Fewer builds also means fewer package downloads that can stall.

**What this makes hard.**
- **These jobs no longer prove the images build from scratch.** Service template, on a push, and `ops/verify/run.sh` keep doing that. A build that breaks only from scratch, such as a pinned base image digest disappearing from Docker Hub, shows up there and not in nightly.
- **A nightly failure may run an image built on an earlier day.** Each job's summary must name every image's key and the commit it was built from.
- **A second set of packages must be kept small.** A scheduled cleanup deletes `mosaiq-ci-*` versions older than 14 days, keeping the newest one per service. If GitHub starts billing container storage, this is where the cost lands.
- **The key must cover exactly what `stage-contracts.sh` stages.** If staging gains a path and the key does not, nightly tests a stale image.

**What must now be true elsewhere.**
- On acceptance, ADR-0040's status becomes `Partially superseded by ADR-0059 (the memory gate and what to drop) and ADR-0080 (images pushed for testing)`, and the index in [`README.md`](README.md) follows.
- Nothing under `deploy/`, no deploy workflow, and nothing on the instance names a `mosaiq-ci-*` image. The instance's read token needs no new access.
- A story implements:
  - a key script next to `stage-contracts.sh`;
  - the pull, build and push steps in `nightly.yml` and `android.yml`, with `packages: write` on those two jobs only;
  - the scheduled cleanup;
  - the `mosaiq-ci-` grep in `.github/scripts/guards.sh`;
  - a check in `deploy/check-services.sh` that reports any container running a `mosaiq-ci-*` image. Today its `mosaiq-` prefix match would accept one.
- The whole-stack step in `service-template.yml` and the image builds in `ops/verify/` keep building from source.

## Compliance

- `git grep -n 'mosaiq-ci-' -- deploy/ .github/workflows/deploy.yml .github/workflows/deploy-service.yml` prints nothing; `.github/scripts/guards.sh` runs it.
- On the instance, `deploy/check-services.sh` reports any container whose image is `ghcr.io/equipo-03-proyecto10/mosaiq-ci-*`.
- `git grep -ln 'mosaiq-ci-' -- .github/workflows/` lists only `nightly.yml`, `android.yml` and the cleanup workflow, and every step that pushes carries `if: github.ref == 'refs/heads/develop'`.
- A test runs the key script over two commits.
  - A change under `services/<name>/`, `contracts/<name>/` or `contracts/common/v1/` changes the affected services' keys and only theirs. A `contracts/common/v1/` change affects every service.
  - A change anywhere else changes no key.
  - The key's inputs are exactly the paths `stage-contracts.sh` copies.
- `.github/workflows/service-template.yml` still runs `compose up --build` on a push, and `ops/verify/` still builds every image.
- Each nightly and emulator job summary lists, for every service, either `pulled <key>` or `built <key>`.
