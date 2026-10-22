package online.maxthecoder.mosaiq.floor.outbox

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import online.maxthecoder.mosaiq.floor.GraphProvider
import online.maxthecoder.mosaiq.floor.auth.TokenStore
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.session.SessionStore

/** What one attempt at a queued write came to. */
sealed interface Attempt {
    /** 2xx: the server holds the write; nothing to resend. */
    data class Done(val status: Int, val body: String?, val correlationId: String?) : Attempt

    /** 409, 422 or any other final 4xx: kept and shown, never resent. */
    data class Refused(val error: ApiError) : Attempt

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
            else -> Refused(ApiError.from(status, body, correlationId, retryAfter))
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
        return drain(graph.outbox, graph.session, graph.tokens, graph.network.client, graph.network.gateway)
    }

    companion object {
        private val JSON = "application/json".toMediaType()

        /** The worker's whole job, outside WorkManager so tests can drive it. */
        suspend fun drain(
            outbox: OutboxDao,
            session: SessionStore,
            tokens: TokenStore,
            client: OkHttpClient,
            gateway: okhttp3.HttpUrl
        ): Result = withContext(Dispatchers.IO) {
            val userId = session.userId.value
            if (userId == null || tokens.read() == null) return@withContext Result.success()
            for (row in outbox.pendingFor(userId)) {
                outbox.countAttempt(row.id)
                val request =
                    Request
                        .Builder()
                        .url(gateway.resolve(row.path)!!)
                        .post(row.body.toRequestBody(JSON))
                        .build()
                val attempt =
                    try {
                        client.newCall(request).execute().use {
                            Attempt.of(
                                it.code,
                                it.body?.string(),
                                it.header(ApiError.CORRELATION_HEADER),
                                it.header("Retry-After")
                            )
                        }
                    } catch (unreachable: IOException) {
                        Attempt.Retry
                    }
                when (attempt) {
                    is Attempt.Done -> outbox.markDone(row.id, attempt.status, attempt.body, attempt.correlationId)
                    is Attempt.Refused ->
                        outbox.markRefused(
                            row.id,
                            attempt.error.status,
                            attempt.error.code,
                            attempt.error.message,
                            attempt.error.correlationId
                        )
                    Attempt.Retry -> return@withContext Result.retry()
                    // Kept; sent again once the session is good (see FloorApplication).
                    Attempt.WaitForSession -> return@withContext Result.success()
                }
            }
            Result.success()
        }
    }
}
