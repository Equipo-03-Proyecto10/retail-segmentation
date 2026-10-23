#!/usr/bin/env bash
# The denied-permission instrumented tests (app/src/androidTest/.../DeniedPermissions.kt).
# connectedAndroidTest leaves them out, because it installs with -g and so
# grants every runtime permission. This installs without -g, revokes them,
# then runs the annotated tests alone.
#
#   cd clients/android && ./gradlew assembleDebug assembleDebugAndroidTest
#   bash run-denied-permission-tests.sh       # one device or emulator attached
#
# am instrument exits 0 even when a test fails, so its report is printed and
# the exit status is 1 unless that report ends in "OK (n tests)".
set -euo pipefail

pkg=online.maxthecoder.mosaiq.floor
adb install -r -t app/build/outputs/apk/debug/app-debug.apk
adb install -r -t app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
for permission in CAMERA ACCESS_FINE_LOCATION ACCESS_COARSE_LOCATION; do
  adb shell pm revoke "$pkg" "android.permission.$permission" || true
  adb shell pm clear-permission-flags "$pkg" "android.permission.$permission" user-set user-fixed || true
done
out=$(adb shell am instrument -w -r -e annotation "$pkg.DeniedPermissions" "$pkg.test/$pkg.SalesTestRunner" | tr -d '\r')
printf '%s\n' "$out"
grep -qE '^OK \([1-9][0-9]* tests?\)$' <<<"$out"
