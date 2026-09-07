# ADR-0012 — `develop` and `main` are joined only by merge commits, never by squash or rebase

**Status:** Accepted
**Owner:** Marcelo
**Issue:** #150
**Supersedes:** —
**Superseded by:** —

---

## Context

Every `develop` → `main` release since #86 has arrived at the same failure: the
pull request opens with a conflict in every file both branches have touched, and
somebody spends the release resolving conflicts between a file and an identical
copy of itself. It happened at #112, at #142, at #145 and again at #150. Two
attempts were made to fix it — #140 and #141, the latter titled *"join the main
history into develop (merge commit, not squash)"* — and both failed, which is
the part worth understanding.

The cause is that the two branches have not shared an ancestor since
2026-08-31. `6a75e9c` (#86) was the last merge with two parents. Every release
commit on `main` after it — `d112cfc`, `ac3fec4`, `e4da24d` — has exactly one
parent, because the release pull request was squash- or rebase-merged. Both
methods copy `develop`'s content onto `main` as brand-new commits with new
SHAs and no link back to the commits they came from. `main` therefore holds the
same *content* as `develop` while sharing none of its *history*.

Git then does what it must. At #150 the merge base of the two branches was
`b266f50` from 2026-08-31, with 49 commits added on `develop` and 5 on `main`
since. Every file created in those 49 commits looks, to the merge, like a file
independently added on both sides — an add/add conflict, on every file, every
release. Resolving it by hand produces no lasting fix, because the next release
starts from the same stale base.

What made this self-perpetuating rather than merely annoying is a settings
interaction nobody had reason to look at. `develop` has
`required_linear_history: true`. GitHub responds by removing the *merge commit*
button from any pull request targeting `develop`, and repository settings have
squash disabled, which leaves **rebase as the only available method**. So the
two deliberate attempts to merge `main` into `develop` were silently flattened
into single-parent commits by the very button the author was obliged to press.
#141's subject line says "merge commit, not squash"; the commit it produced has
one parent. The team's convention was not being ignored — it could not be
carried out.

The judgement call is what to trade for the fix. Requiring merge commits on
`develop` gives up linear history there, and the team chose linear history
deliberately when protection was first configured (`c4a2b63`). A tidy `develop`
log is a real thing to want. But it is worth less than releases that merge, and
the two are not compatible: ancestry is precisely the information a rebase
discards.

## Decision

`develop` and `main` are joined only by true merge commits. `develop` drops
`required_linear_history` so that a merge commit can land on it, and the
repository disables rebase merging so that no pull request into either branch
can be flattened by picking the wrong button — with squash already disabled,
**merge commit becomes the only method the repository offers**. A release is a
`develop` → `main` pull request merged with *Create a merge commit*, and any
hotfix committed on `main` returns to `develop` the same way.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep resolving the conflicts each release | This is the status quo and it is not free: it costs a release slot, and the resolution is a judgement about which copy of an identical file to keep — a place to silently lose a commit. It also never converges, because the merge base does not move. |
| Fix the convention, not the settings — "always use Create a merge commit" | Already tried, twice. #140 and #141 were written to do exactly this and both produced single-parent commits, because `required_linear_history` had removed that button. A convention that the tooling forbids is not a convention. |
| Keep `develop` linear and fast-forward `main` from it instead | Coherent — `main` would always be an ancestor of `develop` — but GitHub offers no fast-forward merge for pull requests. Every release would be a direct `git push origin develop:main` bypassing `main`'s protection, trading a conflict for an unreviewed production deploy. ADR-0011 depends on `main` being reachable only by reviewed pull request. |
| Disable rebase but leave `required_linear_history` on `develop` | Leaves `develop` with no legal merge method at all: linear history forbids merge commits, and squash and rebase would both be off. Nothing could merge. |
| Leave rebase enabled and rely on reviewers | Restores the trap. The release author sees three buttons and one of them silently re-breaks ancestry; the damage is invisible until the *next* release conflicts. Removing the button is the same cost and cannot be forgotten. |

## Consequences

**What this makes easy.** Releases stop conflicting. Once `main` contains
`develop` as an ancestor, the merge base of the next release is the previous
release, so a `develop` → `main` pull request shows only what actually changed —
which also makes it reviewable, something the all-files conflict had destroyed.
`git log main..develop` becomes an honest answer to "what is unreleased", and
`git branch --contains` starts telling the truth about where a commit has
reached.

**What this makes hard.** `develop`'s history stops being linear, and merge
commits from feature branches appear in its log; `git log --graph` on `develop`
is busier to read, and `git log --first-parent develop` becomes the way to see
the integration sequence. Rebase merging is gone repository-wide, so a feature
branch with untidy intermediate commits carries them into `develop` rather than
being replayed clean — tidying now happens on the branch before review, with
`git rebase -i` locally, which is where it was always safer. Nobody can pick
the wrong merge button any more, but nobody can pick a different one for a good
reason either.

**What must now be true elsewhere.** `main` must keep
`required_linear_history: false`, or releases become unmergeable by the method
this record mandates. ADR-0011 is unaffected and still holds: `main` stays
protected and reachable only by reviewed pull request, and the deploy still
triggers on a push to `main` — a merge commit is a push to `main`. The working
agreement in `docs/process.md` §8 carries the rule in prose, and the Sprint
Definition of Done (§6, "`develop` merged to `main` and tagged") now means
merged with a merge commit.

**The 49 commits already stranded are not repaired retroactively.** The merge
commit `b3f8889` on `develop` re-links the two histories from this point
forward; it does not give `main`'s five flattened release commits the parents
they should have had. Those stay as they are, and `git log` will show the same
content arriving twice in that stretch of history.

## Compliance

```bash
# Rebase and squash are both off; merge commit is the only method offered.
gh api repos/Equipo-03-Proyecto10/retail-segmentation \
  --jq 'if .allow_rebase_merge or .allow_squash_merge then "FAIL" else "ok" end'

# develop accepts merge commits.
gh api repos/Equipo-03-Proyecto10/retail-segmentation/branches/develop/protection \
  --jq 'if .required_linear_history.enabled then "FAIL" else "ok" end'

# main still accepts them too, and is still review-gated (ADR-0011).
gh api repos/Equipo-03-Proyecto10/retail-segmentation/branches/main/protection \
  --jq 'if .required_linear_history.enabled then "FAIL" else "ok" end,
        if .required_pull_request_reviews.required_approving_review_count >= 1
        then "ok" else "FAIL" end'

# No flattened copy on main: every commit main has that develop lacks is
# itself a merge. A single-parent commit here is either a squashed release or
# a hotfix that never came back -- both are how the branches drift apart.
git fetch origin --quiet
test -z "$(git rev-list --no-merges origin/develop..origin/main)" && echo ok

# The merge base tracks the last release rather than standing still. Compare
# it across two releases: if it has not moved, ancestry is being discarded.
git log -1 --format='%h %ad' --date=short $(git merge-base origin/main origin/develop)

# The release itself merges clean — no conflict, without resolving anything.
git merge-tree --write-tree origin/main origin/develop >/dev/null && echo ok
```

The last two are the ones that matter: they fail exactly when the problem this
record exists to prevent has come back.
