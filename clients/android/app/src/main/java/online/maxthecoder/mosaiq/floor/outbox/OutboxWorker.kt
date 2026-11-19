package online.maxthecoder.mosaiq.floor.outbox

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import online.maxthecoder.mosaiq.floor.AppText
import online.maxthecoder.mosaiq.floor.GraphProvider
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.auth.TokenStore
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.session.SessionStore

/** What one attempt at a queued write came to. */
sealed interface Attempt {
    /** 2xx: the server holds the write; nothing to resend. */
    data class Done(
        val status: Int,
        val body: String?,
        val correlationId: String?,
        val replayed: Boolean = false
    ) : Attempt

    /**
     * 409, 422 or any other final 4xx: kept and shown, never resent. [body] is the error envelope as
     * it came, kept so a screen can name the line a refusal points at.
     */
    data class Refused(val error: ApiError, val body: String? = null) : Attempt

    /**
     * A network error, a timeout or a 503, and their gateway forms: a 504 (the
     * service may have kept the write, so only a resend with the same id can
     * tell), a 502, or a 429. None is a final answer; resend later.
     */
    data object Retry : Attempt

    /** 401: wait for the refresh, or for the same user to sign in again. */
    data object WaitForSession : Attempt

    companion object {
        private val RESEND = setOf(429, 502, 503, 504)

        fun of(status: Int, body: String?, correlationId: String?, retryAfter: String?): Attempt = when {
            status in 200..299 -> Done(status, body, correlationId)
            status == 401 -> WaitForSession
            status in RESEND -> Retry
            else -> Refused(ApiError.from(status, body, correlationId, retryAfter), body)
        }
    }
}

/**
 * Sends the signed-in user's queued writes, oldest first, each exactly as it
 * was stored. Rows another user queued are left alone: a write must carry the
 * token of the user who made it (ADR-0041).
 */
class OutboxWorker(
    context: Context,
    parameters: WorkerParameters
) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val graph = (applicationContext as GraphProvider).graph
        val result = drain(
            graph.outbox,
            graph.session,
            graph.tokens,
            graph.network.client,
            graph.network.gateway,
            graph.inFlight
        )
        // "Send now" is one attempt. Backing off here would hold its unique work, and KEEP would drop
        // every tap until the backoff ended; the resend belongs to the chain, which backs off for it.
        if (result == Result.retry() && FloorWrites.NOW_WORK_NAME in tags) {
            graph.kick()
            return Result.success()
        }
        return result
    }

    companion object {
        private val JSON = "application/json".toMediaType()

        /** One drain at a time, so a manual send and the chained worker never send the same row twice. */
        private val drainLock = Mutex()

        /** The worker's whole job, outside WorkManager so tests can drive it. */
        suspend fun drain(
            outbox: OutboxDao,
            session: SessionStore,
            tokens: TokenStore,
            client: OkHttpClient,
            gateway: okhttp3.HttpUrl,
            inFlight: InFlight = InFlight()
        ): Result = withContext(Dispatchers.IO) {
            drainLock.withLock { sendAll(outbox, session, tokens, client, gateway, inFlight) }
        }

        private suspend fun sendAll(
            outbox: OutboxDao,
            session: SessionStore,
            tokens: TokenStore,
            client: OkHttpClient,
            gateway: okhttp3.HttpUrl,
            inFlight: InFlight
        ): Result {
            val userId = session.userId.value
            if (userId == null || tokens.read() == null) return Result.success()
            for (row in outbox.pendingFor(userId)) {
                outbox.countAttempt(row.id)
                val url = gateway.resolve(row.path)
                if (url == null) {
                    // A path that is no URL never becomes one: refuse the row rather than retry it forever.
                    outbox.markRefused(row.id, 0, "invalid_path", AppText.get(R.string.outbox_invalid_path), null)
                    continue
                }
                val request =
                    Request
                        .Builder()
                        .url(url)
                        .post(row.body.toRequestBody(JSON))
                        .apply {
                            if (row.operation == Operation.SALE) header("Idempotency-Key", row.clientId)
                        }
                        .build()
                // Marked until the row's new state is written, so the badge never falls back to "Waiting".
                inFlight.begin(row.id)
                try {
                    val attempt =
                        run {
                            try {
                                client.newCall(request).execute().use {
                                    val verdict = Attempt.of(
                                        it.code,
                                        it.body?.string(),
                                        it.header(ApiError.CORRELATION_HEADER),
                                        it.header("Retry-After")
                                    )
                                    if (verdict is Attempt.Done) {
                                        verdict.copy(replayed = it.header("Idempotent-Replayed") == "true")
                                    } else {
                                        verdict
                                    }
                                }
                            } catch (unreachable: IOException) {
                                Attempt.Retry
                            }
                        }
                    when (attempt) {
                        is Attempt.Done -> outbox.markDone(
                            row.id,
                            attempt.status,
                            attempt.body,
                            attempt.correlationId,
                            if (attempt.replayed) "idempotent_replayed" else null
                        )
                        is Attempt.Refused ->
                            outbox.markRefused(
                                row.id,
                                attempt.error.status,
                                attempt.error.code,
                                attempt.error.message,
                                attempt.error.correlationId,
                                attempt.body
                            )
                        Attempt.Retry -> return Result.retry()
                        // Kept; sent again once the session is good (see FloorApplication).
                        Attempt.WaitForSession -> return Result.success()
                    }
                } finally {
                    inFlight.end(row.id)
                }
            }
            return Result.success()
        }
    }
}
