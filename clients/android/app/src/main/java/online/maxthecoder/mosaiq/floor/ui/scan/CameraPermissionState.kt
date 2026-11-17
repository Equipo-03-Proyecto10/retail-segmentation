package online.maxthecoder.mosaiq.floor.ui.scan

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import online.maxthecoder.mosaiq.floor.ui.CameraPermission

/**
 * Where the camera stands after the system is asked again: a grant made in
 * Settings counts, and a revoke turns a granted camera into a denied one. A
 * camera never asked about stays unasked until the user taps Scan.
 */
fun reconcilePermission(current: CameraPermission, granted: Boolean): CameraPermission = when {
    granted -> CameraPermission.GRANTED
    current == CameraPermission.GRANTED -> CameraPermission.DENIED
    else -> current
}

/** Opens this app's page in the system settings, where the camera can be allowed. */
fun openAppSettings(context: Context) {
    val intent =
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    try {
        context.startActivity(intent)
    } catch (_: ActivityNotFoundException) {
        // A device without a settings app has nothing to open; the manual entry stays available.
    }
}
