package online.maxthecoder.mosaiq.floor.net

import kotlinx.serialization.json.Json
import online.maxthecoder.mosaiq.floor.AppText
import online.maxthecoder.mosaiq.floor.R
import retrofit2.Response

/** JSON as every service speaks it: a field added within v1 never breaks the app. */
val json =
    Json {
        ignoreUnknownKeys = true
        explicitNulls = false
    }

/**
 * A refusal as the screen shows it: always the envelope's code and message
 * and the request's correlation id, so a support question can name the call.
 */
data class ApiError(
    val status: Int,
    val code: String,
    val message: String,
    val correlationId: String?,
    val details: List<ErrorDetail> = emptyList(),
    val retryAfterSeconds: Long? = null
) {
    /**
     * True when asking again may get a different answer: no answer at all, a rate limit, or a server
     * or gateway fault. A 4xx refusal says the same thing the next time, so Retry is not offered for it.
     */
    val retryable: Boolean
        get() = code == "unreachable" || status == TOO_MANY_REQUESTS || status >= SERVER_FAULT

    companion object {
        const val CORRELATION_HEADER = "X-Correlation-ID"

        fun from(response: Response<*>): ApiError = from(
            status = response.code(),
            body = response.errorBody()?.string(),
            correlationHeader = response.headers()[CORRELATION_HEADER],
            retryAfter = response.headers()["Retry-After"]
        )

        fun from(status: Int, body: String?, correlationHeader: String?, retryAfter: String?): ApiError {
            val envelope =
                body
                    ?.let { runCatching { json.decodeFromString(ErrorEnvelope.serializer(), it) }.getOrNull() }
            return ApiError(
                status = status,
                code = envelope?.code ?: "http_$status",
                message =
                transientMessage(status, retryAfter?.trim()?.toLongOrNull())
                    ?: envelope?.message
                    ?: AppText.get(R.string.error_answered, status),
                correlationId = envelope?.correlationId ?: correlationHeader,
                details = envelope?.details.orEmpty(),
                retryAfterSeconds = retryAfter?.trim()?.toLongOrNull()
            )
        }

        /**
         * What a gateway or a server fault says in plain words, and what happens next. A 429 and a
         * 5xx carry text meant for operators (or none at all), and every screen that shows an
         * [ApiError] shows this message, so it is written once, here (F27-13). A 4xx refusal keeps the
         * service's own message: it names what the person can fix.
         */
        private fun transientMessage(status: Int, retryAfter: Long?): String? = when {
            status == TOO_MANY_REQUESTS && retryAfter != null && retryAfter > 0 ->
                AppText.plural(
                    R.plurals.error_rate_limited_after,
                    retryAfter.coerceAtMost(Int.MAX_VALUE.toLong()).toInt(),
                    retryAfter
                )
            status == TOO_MANY_REQUESTS -> AppText.get(R.string.error_rate_limited)
            status in GATEWAY_FAULTS -> AppText.get(R.string.error_unavailable)
            status >= SERVER_FAULT -> AppText.get(R.string.error_server)
            else -> null
        }

        private const val TOO_MANY_REQUESTS = 429
        private const val SERVER_FAULT = 500
        private val GATEWAY_FAULTS = setOf(502, 503, 504)

        /** The call failed in a way no answer describes; the client cannot say more. */
        fun unexpected(): ApiError = ApiError(
            status = 0,
            code = "unexpected",
            message = AppText.get(R.string.error_unexpected),
            correlationId = null
        )

        /** No answer at all: the network or the gateway never replied. */
        fun unreachable(correlationId: String?): ApiError = ApiError(
            status = 0,
            code = "unreachable",
            message = AppText.get(R.string.error_unreachable),
            correlationId = correlationId
        )
    }
}
