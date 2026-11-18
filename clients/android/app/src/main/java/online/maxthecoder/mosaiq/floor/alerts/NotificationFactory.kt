package online.maxthecoder.mosaiq.floor.alerts

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import online.maxthecoder.mosaiq.floor.MainActivity
import online.maxthecoder.mosaiq.floor.R

/**
 * The one place a notification is built (ADR-0071 section 2). It takes a closed [AlertKind], a count and at
 * most one campaign name, so no caller can put a customer, a staff member, an amount or any typed text in
 * one. Every notification is private on the lock screen and carries a public version that says only that
 * there are updates; the full version is just as free of personal data, because a lock screen that is not
 * secure shows it.
 */
object NotificationFactory {
    const val CHANNEL_CAMPAIGNS = "campaign_alerts"
    const val CHANNEL_INCIDENTS = "capture_incidents"
    const val CHANNEL_ERRORS = "capture_errors"
    const val CHANNEL_SESSION = "session"

    const val EXTRA_KIND = "online.maxthecoder.mosaiq.floor.alert.KIND"
    const val EXTRA_SUBJECT = "online.maxthecoder.mosaiq.floor.alert.SUBJECT"

    fun channelOf(kind: AlertKind): String = when (kind) {
        AlertKind.CAMPAIGN_STARTED, AlertKind.CAMPAIGN_ENDING -> CHANNEL_CAMPAIGNS
        AlertKind.CAPTURE_INCIDENT -> CHANNEL_INCIDENTS
        AlertKind.CAPTURE_ERROR -> CHANNEL_ERRORS
        AlertKind.SESSION_ENDED, AlertKind.BASELINE -> CHANNEL_SESSION
    }

    /** The four channels, each private on the lock screen. Safe to call again: Android keeps the user's settings. */
    fun createChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        listOf(
            Triple(CHANNEL_CAMPAIGNS, R.string.channel_campaigns, NotificationManager.IMPORTANCE_DEFAULT),
            Triple(CHANNEL_INCIDENTS, R.string.channel_incidents, NotificationManager.IMPORTANCE_DEFAULT),
            Triple(CHANNEL_ERRORS, R.string.channel_errors, NotificationManager.IMPORTANCE_DEFAULT),
            Triple(CHANNEL_SESSION, R.string.channel_session, NotificationManager.IMPORTANCE_LOW)
        ).forEach { (id, name, importance) ->
            val channel = NotificationChannel(id, context.getString(name), importance)
            channel.lockscreenVisibility = Notification.VISIBILITY_PRIVATE
            manager.createNotificationChannel(channel)
        }
    }

    /** The text of the full version; the public one is always [R.string.notification_public_text]. */
    fun text(context: Context, kind: AlertKind, count: Int, campaignName: String?): String = when (kind) {
        AlertKind.CAMPAIGN_STARTED -> context.getString(R.string.notification_campaign_started, campaignName.orEmpty())
        AlertKind.CAMPAIGN_ENDING -> context.getString(R.string.notification_campaign_ending, campaignName.orEmpty())
        AlertKind.CAPTURE_INCIDENT ->
            context.resources.getQuantityString(R.plurals.notification_capture_incidents, count, count)
        AlertKind.CAPTURE_ERROR -> context.resources.getQuantityString(
            R.plurals.notification_capture_errors,
            count,
            count
        )
        AlertKind.SESSION_ENDED, AlertKind.BASELINE -> context.getString(R.string.notification_session_ended)
    }

    /**
     * One notification of [kind]. Tapping it opens the app at its updates screen with [kind] and one opaque
     * [subjectId] (a `campaign_id` or an `incident_id`), nothing else.
     */
    fun buildNotification(
        context: Context,
        kind: AlertKind,
        count: Int,
        campaignName: String? = null,
        subjectId: String? = null
    ): Notification {
        val title = context.getString(R.string.notification_title)
        val open =
            Intent(context, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .putExtra(EXTRA_KIND, kind.name)
                .apply { subjectId?.let { putExtra(EXTRA_SUBJECT, it) } }
        val pending =
            PendingIntent.getActivity(
                context,
                requestCode(kind, subjectId),
                open,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
        val public =
            NotificationCompat.Builder(context, channelOf(kind))
                .setSmallIcon(R.drawable.ic_launcher_monochrome)
                .setContentTitle(title)
                .setContentText(context.getString(R.string.notification_public_text))
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                .setLocalOnly(true)
                .build()
        return NotificationCompat.Builder(context, channelOf(kind))
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(title)
            .setContentText(text(context, kind, count, campaignName))
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(public)
            .setLocalOnly(true)
            .setOnlyAlertOnce(kind == AlertKind.SESSION_ENDED)
            .setAutoCancel(true)
            .setContentIntent(pending)
            .build()
    }

    /** The id a notification is posted under: one per campaign alert, one for each other kind. */
    fun notificationId(kind: AlertKind, subjectId: String?): Int = when (kind) {
        AlertKind.CAMPAIGN_STARTED, AlertKind.CAMPAIGN_ENDING -> requestCode(kind, subjectId)
        else -> kind.ordinal + 1
    }

    private fun requestCode(kind: AlertKind, subjectId: String?): Int =
        31 * (kind.ordinal + 1) + (subjectId?.hashCode() ?: 0)
}
