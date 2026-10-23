package online.maxthecoder.mosaiq.floor

/**
 * A test that needs the app's runtime permissions not granted, so that the
 * system dialog appears and the test can refuse it. `connectedAndroidTest`
 * installs with `-g`, which grants them, and revoking one from inside the test
 * would kill the process it runs in. So the build leaves these out of it, and
 * the emulator job runs them alone: an install without `-g`, `pm revoke`, then
 * `am instrument -e annotation online.maxthecoder.mosaiq.floor.DeniedPermissions`.
 */
@Retention(AnnotationRetention.RUNTIME)
@Target(AnnotationTarget.CLASS, AnnotationTarget.FUNCTION)
annotation class DeniedPermissions
