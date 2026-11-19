package online.maxthecoder.mosaiq.floor.outbox

import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import java.time.Instant
import java.time.OffsetDateTime
import java.util.UUID
import java.util.concurrent.TimeUnit
import online.maxthecoder.mosaiq.floor.net.CartEventRequest
import online.maxthecoder.mosaiq.floor.net.CheckInRequest
import online.maxthecoder.mosaiq.floor.net.EngagementEventRequest
import online.maxthecoder.mosaiq.floor.net.SaleLineRequest
import online.maxthecoder.mosaiq.floor.net.SaleRequest
import online.maxthecoder.mosaiq.floor.net.Services
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.scan.Scan
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

    /**
     * Queues one QR_SCAN for a product that was found. The client event id is
     * the scan's own, fixed when the code was decoded, never a new one here.
     * `customerId` stays null until a customer is selected (F17-04).
     */
    suspend fun queueQrScan(scan: Scan, productId: Int, storeId: Int, customerId: String? = null): Long {
        val userId = checkNotNull(session.userId.value) { "No user is signed in." }
        val request =
            EngagementEventRequest(
                clientEventId = scan.clientEventId,
                type = "QR_SCAN",
                storeId = storeId,
                productId = productId,
                occurredAt = scan.occurredAt.toString(),
                customerId = customerId
            )
        val id =
            outbox.insert(
                OutboxRow(
                    operation = Operation.QR_SCAN,
                    path = Services.ENGAGEMENT + "engagement-events",
                    body = json.encodeToString(EngagementEventRequest.serializer(), request),
                    clientId = request.clientEventId,
                    userId = userId,
                    createdAt = scan.occurredAt.toEpochMilli()
                )
            )
        schedule()
        return id
    }

    /**
     * Queues one RECO_CLICK for a tapped recommendation (F17-05). The client
     * event id is generated here, once, at the tap: a retry resends it, and a
     * second tap on the same item is a second click with a new id.
     */
    suspend fun queueRecoClick(
        customerId: String,
        storeId: Int,
        productId: Int,
        labelCode: String,
        rank: Int,
        now: Instant = Instant.now()
    ): Long {
        val userId = checkNotNull(session.userId.value) { "No user is signed in." }
        require(rank >= 1) { "A rank is 1-based." }
        val request =
            EngagementEventRequest(
                clientEventId = UUID.randomUUID().toString(),
                type = "RECO_CLICK",
                storeId = storeId,
                productId = productId,
                occurredAt = now.toString(),
                customerId = customerId,
                labelCode = labelCode,
                rank = rank
            )
        val id =
            outbox.insert(
                OutboxRow(
                    operation = Operation.RECO_CLICK,
                    path = Services.ENGAGEMENT + "engagement-events",
                    body = json.encodeToString(EngagementEventRequest.serializer(), request),
                    clientId = request.clientEventId,
                    userId = userId,
                    createdAt = now.toEpochMilli()
                )
            )
        schedule()
        return id
    }

    /**
     * Queues one `CART_ADD` or `CART_REMOVE` through engagement v2 (F27-05): a cart line added,
     * raised, lowered or removed. It is intent, never a sale, and it never reaches `POST /v1/sales`.
     * The client event id is generated here, once, at the tap.
     */
    suspend fun queueCartEvent(
        operation: Operation,
        storeId: Int,
        productId: Int,
        quantity: Int,
        customerId: String? = null,
        now: Instant = Instant.now()
    ): Long {
        require(operation == Operation.CART_ADD || operation == Operation.CART_REMOVE) { "Not a cart event." }
        require(quantity in 1..MAX_CART_QUANTITY) { "A cart event moves 1 to $MAX_CART_QUANTITY units." }
        val userId = checkNotNull(session.userId.value) { "No user is signed in." }
        val request =
            CartEventRequest(
                clientEventId = UUID.randomUUID().toString(),
                type = operation.name,
                storeId = storeId,
                productId = productId,
                quantity = quantity,
                occurredAt = now.toString(),
                customerId = customerId
            )
        val id =
            outbox.insert(
                OutboxRow(
                    operation = operation,
                    path = Services.ENGAGEMENT_V2 + "engagement-events",
                    body = json.encodeToString(CartEventRequest.serializer(), request),
                    clientId = request.clientEventId,
                    userId = userId,
                    createdAt = now.toEpochMilli()
                )
            )
        schedule()
        return id
    }

    /**
     * Captures the complete sale at the confirming tap. The UUID is generated
     * once here and stored as both the row identity and the Idempotency-Key.
     */
    suspend fun queueSale(
        customerId: String,
        storeId: Int,
        channelId: Int,
        lines: List<SaleLineRequest>,
        now: OffsetDateTime = OffsetDateTime.now(),
        newId: () -> UUID = UUID::randomUUID
    ): Long {
        val userId = checkNotNull(session.userId.value) { "No user is signed in." }
        val key = newId().toString().lowercase()
        val request = SaleRequest(customerId, storeId, channelId, now.toString(), lines)
        val id =
            outbox.insert(
                OutboxRow(
                    operation = Operation.SALE,
                    path = Services.SALES + "sales",
                    body = json.encodeToString(SaleRequest.serializer(), request),
                    clientId = key,
                    userId = userId,
                    createdAt = now.toInstant().toEpochMilli()
                )
            )
        schedule()
        return id
    }

    companion object {
        const val WORK_NAME = "floor-writes"

        /** `quantity` maximum of a cart event in contracts/engagement v2. */
        const val MAX_CART_QUANTITY = 1000

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

        const val NOW_WORK_NAME = "floor-writes-now"

        /**
         * "Send now": its own unique work, so it neither waits behind the chain's backoff nor cancels a
         * worker mid-send (REPLACE would). KEEP means repeated taps do not queue extra drains; the drain
         * lock serialises it with the chain. Tagged so the worker knows it: a row that must be resent is
         * handed to the chain instead of backing this work off (see [OutboxWorker.doWork]).
         */
        fun immediate(workManager: WorkManager): () -> Unit = {
            workManager.enqueueUniqueWork(
                NOW_WORK_NAME,
                ExistingWorkPolicy.KEEP,
                OneTimeWorkRequestBuilder<OutboxWorker>()
                    .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                    .addTag(NOW_WORK_NAME)
                    .build()
            )
        }

        private const val BACKOFF_SECONDS = 10L
    }
}
