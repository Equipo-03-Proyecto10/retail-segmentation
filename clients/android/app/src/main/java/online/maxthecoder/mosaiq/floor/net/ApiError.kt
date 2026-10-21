package online.maxthecoder.mosaiq.floor.net

import kotlinx.serialization.json.Json
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
    val retryAfterSeconds: Long? = null
) {
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
                message = envelope?.message ?: "The service answered $status.",
                correlationId = envelope?.correlationId ?: correlationHeader,
                retryAfterSeconds = retryAfter?.trim()?.toLongOrNull()
            )
        }

        /** No answer at all: the network or the gateway never replied. */
        fun unreachable(correlationId: String?): ApiError = ApiError(
            status = 0,
            code = "unreachable",
            message = "The service could not be reached. Check the connection and try again.",
            correlationId = correlationId
        )
    }
}
