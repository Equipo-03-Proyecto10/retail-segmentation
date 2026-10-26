# ADR-0058 — The Android client navigates with saved plain state, not Navigation-Compose

**Status:** Proposed
**Owner:** Max
**Issue:** #717 (UX-01), epic #716; implemented by #723 (UX-05)
**Supersedes:** —
**Superseded by:** —

---

## Context

The app has no navigation library and no back stack. `FloorApp` switches the
screen on the session state, and `FloorHome` switches a four-value local
`enum Tab { CHECK_IN, SCAN, SALE, OUTBOX }` held in `remember` (`FloorScreens.kt:41,53`).
The consequences are bugs, not style: the selected tab, the city field and the
"picking a store" flag are lost on rotation, because `remember` is not saved
(`FloorScreens.kt:53,136,149`); there is no `BackHandler`, so Back from any
tab leaves the app; and a product opened from a recommendation (`onOpenProduct`)
flips the tab with no way back.

UX-05 has to fix this, and the obvious choice is Navigation-Compose. Two
facts make it less obvious here:

- **View models are created up front.** `MainActivity.kt:26-40` builds all five
  view models once, and each carries a multi-user guard that drops in-flight
  results when the signed-in user changes (found as regressions in
  `docs/evidence/430-android-pr-review.md`). Navigation-Compose scopes view
  models to back-stack entries, which would change when each is created and
  cleared, and those guards were the source of two regressions already.
- **The destination set is small and flat.** Four root sections and a few
  sub-states (picking a store, registering a customer, a product opened from a
  recommendation). There are no deep links: push notifications are out of
  scope (`docs/scope-delivery-2-distributed.md` §8).

What is genuinely uncertain is growth. If the app gains several more sections
or needs deep links, a hand-written back stack stops being cheaper.

## Decision

The Android client keeps plain state for navigation and makes it survive
recreation. `FloorHome` holds the current location as a sealed
`FloorLocation` (four roots and their sub-states), saved with
`rememberSaveable` and an explicit `Saver`, and shows a bottom navigation bar
that marks the selected root. A `BackHandler` pops in this order: a sub-state
returns to its root, a root other than Check-in returns to Check-in, and only
Check-in leaves the app. State that belongs to a screen's inputs (the city
field, the picking flag) is `rememberSaveable` in that screen. The view models
and their construction in `MainActivity` do not change. No navigation
dependency is added.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Navigation-Compose with a `NavHost` | A new dependency to solve a problem of about six destinations, and it re-scopes view models whose creation order is part of the multi-user guard. It earns its place with deep links or a much larger graph, neither of which is planned. |
| Keep `remember` and add only a `BackHandler` | Leaves rotation and process death resetting the user to Check-in, which is the bug. |
| Make the activity handle `configChanges` so it is never recreated | Hides rotation but not process death, and it is a known source of layout and resource bugs. |

## Consequences

**What this makes easy.** No new dependency or version-catalog entry. The view
model wiring and its guards stay as they are, so the sign-out and
user-switch tests keep their meaning. Restoration is testable with
`StateRestorationTester` in a few lines.

**What this makes hard.** The back stack is ours: every new sub-state must say
what Back does, and a reviewer has to check it. There are no deep links. If
the app grows past roughly eight destinations, or needs a link, this decision
should be superseded by one that adopts Navigation-Compose and says how the
view model scoping is handled.

**What must now be true elsewhere.** UX-05 (#723) implements this record, and
UX-07, UX-08 and UX-09 put their sub-states into `FloorLocation` instead of
local `remember` flags. The UX-16 tests (#732) cover rotation and Back.

## Compliance

1. A Compose test using `StateRestorationTester` selects each root and a
   sub-state, recreates the content, and asserts the selection survived.
2. A test presses Back from a sub-state, from a non-Check-in root and from
   Check-in, and asserts the three outcomes above.
3. `clients/android/gradle/libs.versions.toml` has no `navigation` entry; a
   reviewer sees one added in a diff and asks for the superseding ADR.
