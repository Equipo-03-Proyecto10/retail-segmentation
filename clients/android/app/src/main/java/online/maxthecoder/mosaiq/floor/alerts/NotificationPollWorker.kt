package online.maxthecoder.mosaiq.floor.alerts

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit
import online.maxthecoder.mosaiq.floor.GraphProvider

/**
 * ADR-0071's periodic pull. It reads with the app's own client, so a refresh goes through the single-flight
 * authenticator and never extends the session; it posts nothing on a failure it will retry.
 */
class NotificationPollWorker(
    context: Context,
    parameters: WorkerParameters
) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result = when ((applicationContext as GraphProvider).graph.alerts.poll()) {
        PollOutcome.RETRY -> Result.retry()
        // A session that ended has already cancelled this work; there is nothing to retry.
        PollOutcome.DONE, PollOutcome.SESSION_ENDED, PollOutcome.SIGNED_OUT -> Result.success()
    }

    companion object {
        const val WORK_NAME = "mosaiq-notification-poll"

        /** The platform minimum, and the flex window inside it. */
        const val INTERVAL_MINUTES = 15L
        const val FLEX_MINUTES = 5L
        const val BACKOFF_MINUTES = 5L

        /** Enqueued once per sign-in; KEEP leaves a running schedule alone. */
        fun scheduler(workManager: WorkManager): () -> Unit = {
            workManager.enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<NotificationPollWorker>(
                    INTERVAL_MINUTES,
                    TimeUnit.MINUTES,
                    FLEX_MINUTES,
                    TimeUnit.MINUTES
                ).setConstraints(
                    Constraints
                        .Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .setRequiresBatteryNotLow(true)
                        .build()
                ).setBackoffCriteria(BackoffPolicy.EXPONENTIAL, BACKOFF_MINUTES, TimeUnit.MINUTES)
                    .build()
            )
        }

        fun canceller(workManager: WorkManager): () -> Unit = { workManager.cancelUniqueWork(WORK_NAME) }
    }
}
