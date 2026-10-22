# #430 — Android review of PR #657

Local review on 2026-10-22, Linux, JDK 21 and Android SDK platform 36:
`./gradlew ktlintCheck lint testDebugUnitTest assembleDebug`.

Two regression tests failed on the PR as submitted (58 tests, 2 failed):

- A scan still in flight when its user signed out queued its QR_SCAN under
  the next user who signed in.
- The next user could not scan the code the previous user scanned last,
  because the repeat guard survived the sign-out.

`ScanViewModel` now records the user who started each lookup, drops the
result and the queued event when a different user is signed in by the time
the lookups answer, and resets the repeat guard when the signed-in user
changes. Only the latest scan's answer is shown.

Also: the screen's tab row scrolls horizontally on narrow phones, the manual
product id field takes the free width next to its button, and
`LocalLifecycleOwner` comes from `androidx.lifecycle.compose` instead of the
deprecated `androidx.compose.ui.platform` alias.

After the fix: ktlint, Android lint, and **all 58 unit tests pass**;
`assembleDebug` builds. CI has no Android job, so this run is the only check.
Scanning a real QR code on a device was not repeated in this review.
