package online.maxthecoder.mosaiq.floor.alerts

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/** Where alerts go. Without permission they go nowhere, and the updates screen still shows them. */
interface Notifier {
    /** Posts (or replaces) one alert; `false` when the system would not show it. */
    fun post(kind: AlertKind, count: Int, campaignName: String? = null, subjectId: String? = null): Boolean

    fun cancel(kind: AlertKind, subjectId: String? = null)

    fun cancelAll()

    /** Whether a posted alert would show: the permission (Android 13+) and the user's app setting. */
    fun enabled(): Boolean

    companion object {
        /** Posts nothing: for tests and graphs that never notify. */
        val NONE: Notifier =
            object : Notifier {
                override fun post(kind: AlertKind, count: Int, campaignName: String?, subjectId: String?) = false

                override fun cancel(kind: AlertKind, subjectId: String?) = Unit

                override fun cancelAll() = Unit

                override fun enabled() = false
            }
    }
}

/** The platform's notifications, built only by [NotificationFactory]. */
class SystemNotifier(
    context: Context
) : Notifier {
    private val context = context.applicationContext
    private val manager = NotificationManagerCompat.from(this.context)

    init {
        NotificationFactory.createChannels(this.context)
    }

    override fun enabled(): Boolean {
        val granted =
            Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
        return granted && manager.areNotificationsEnabled()
    }

    override fun post(kind: AlertKind, count: Int, campaignName: String?, subjectId: String?): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            return false
        }
        if (!manager.areNotificationsEnabled()) return false
        val notification = NotificationFactory.buildNotification(context, kind, count, campaignName, subjectId)
        return try {
            manager.notify(NotificationFactory.notificationId(kind, subjectId), notification)
            true
        } catch (revoked: SecurityException) {
            // The permission was withdrawn between the check and the post.
            false
        }
    }

    override fun cancel(kind: AlertKind, subjectId: String?) {
        manager.cancel(NotificationFactory.notificationId(kind, subjectId))
    }

    override fun cancelAll() {
        manager.cancelAll()
    }
}
