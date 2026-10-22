package online.maxthecoder.mosaiq.floor.outbox

import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import java.time.Instant
import java.util.UUID
import java.util.concurrent.TimeUnit
import online.maxthecoder.mosaiq.floor.net.CheckInRequest
import online.maxthecoder.mosaiq.floor.net.Services
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.session.SessionStore

/** What the phone measured. No fix at all is `null`, never zeros. */
data class Fix(
    val latitude: Double,
    val longitude: Double,
    val accuracyM: Double
)

/**
 * Puts floor writes in the queue. The client id is generated here, once, at
 * the moment of the tap (ADR-0041), and stored with the row: the worker only
 * ever resends what was stored, so a retry can never become a second write.
 */
class FloorWrites(
    private val outbox: OutboxDao,
    private val session: SessionStore,
    private val schedule: () -> Unit
) {
    /** Queues a check-in for the signed-in user; returns the row's id. */
    suspend fun queueCheckIn(storeId: Int, fix: Fix?, now: Instant = Instant.now()): Long {
        val userId = checkNotNull(session.userId.value) { "No user is signed in." }
        val request =
            CheckInRequest(
                clientCheckInId = UUID.randomUUID().toString(),
                storeId = storeId,
                occurredAt = now.toString(),
                latitude = fix?.latitude,
                longitude = fix?.longitude,
                accuracyM = fix?.accuracyM
            )
        val id =
            outbox.insert(
                OutboxRow(
                    operation = Operation.CHECK_IN,
                    path = Services.CHECK_INS + "check-ins",
                    body = json.encodeToString(CheckInRequest.serializer(), request),
                    clientId = request.clientCheckInId,
                    userId = userId,
                    createdAt = now.toEpochMilli()
                )
            )
        schedule()
        return id
    }

    companion object {
        const val WORK_NAME = "floor-writes"

        /** One chain at a time, only when a network is connected, with backoff. */
        fun scheduler(workManager: WorkManager): () -> Unit = {
            workManager.enqueueUniqueWork(
                WORK_NAME,
                ExistingWorkPolicy.APPEND_OR_REPLACE,
                OneTimeWorkRequestBuilder<OutboxWorker>()
                    .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                    .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, BACKOFF_SECONDS, TimeUnit.SECONDS)
                    .build()
            )
        }

        private const val BACKOFF_SECONDS = 10L
    }
}
