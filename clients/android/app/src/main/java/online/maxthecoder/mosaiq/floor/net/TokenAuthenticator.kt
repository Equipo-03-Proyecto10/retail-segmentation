package online.maxthecoder.mosaiq.floor.net

import okhttp3.Authenticator
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.Route
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.TokenStore
import online.maxthecoder.mosaiq.floor.auth.Tokens

/**
 * The only path that refreshes tokens, single-flight (ADR-0036, ADR-0041).
 *
 * Refresh tokens rotate, and reusing one ends the session, so two racing
 * refreshes would sign the user out. Every request that gets 401
 * `invalid_token` comes here and waits on one lock. The first one in
 * refreshes; the rest find the stored access token already differs from the
 * one they sent and simply retry with it. Each request is retried once.
 *
 * A refresh refused for good (`invalid_refresh`, `refresh_reused`,
 * `session_expired`, `session_revoked`) erases the tokens and signs the user
 * out with a reason. A refresh that cannot be answered now (503
 * `auth_unavailable`, `database_unavailable`, or no answer) keeps them, and is
 * left in [lastFailure] for the screen to show.
 */
class TokenAuthenticator(
    private val baseUrl: HttpUrl,
    private val tokens: TokenStore,
    private val events: SessionEvents,
    private val refreshClient: OkHttpClient
) : Authenticator {
    private val lock = Any()

    @Volatile var lastFailure: ApiError? = null
        private set

    override fun authenticate(route: Route?, response: Response): Request? {
        val sent = response.request.header("Authorization")?.removePrefix("Bearer ") ?: return null
        val code = ApiError.from(response.code, response.peekBody(MAX_ERROR_BYTES).string(), null, null).code
        if (code == "session_revoked") {
            // The service found the session ended: refreshing cannot bring it back.
            // Read before the retry guard, so a session revoked between a refresh
            // and its retry still signs out.
            endSession("session_revoked")
            return null
        }
        if (response.priorResponse != null) return null
        if (code != "invalid_token") return null
        synchronized(lock) {
            val current = tokens.read() ?: return null
            if (current.accessToken != sent) return retry(response.request, current)
            val refreshed = refresh(current) ?: return null
            return retry(response.request, refreshed)
        }
    }

    private fun refresh(current: Tokens): Tokens? {
        val body = json.encodeToString(RefreshRequest.serializer(), RefreshRequest(current.refreshToken))
        val request =
            Request
                .Builder()
                .url(baseUrl.resolve(Services.AUTH + "sessions/refresh")!!)
                .post(body.toRequestBody(JSON))
                .build()
        val answer =
            runCatching { refreshClient.newCall(request).execute() }.getOrElse {
                lastFailure = ApiError.unreachable(null)
                return null
            }
        answer.use {
            val text = it.body?.string()
            if (it.code == 200 && text != null) {
                val result = json.decodeFromString(TokenResult.serializer(), text)
                val rotated = Tokens(result.accessToken, result.refreshToken)
                tokens.write(rotated)
                lastFailure = null
                return rotated
            }
            val error = ApiError.from(it.code, text, it.header(ApiError.CORRELATION_HEADER), it.header("Retry-After"))
            lastFailure = error
            if (it.code == 401 && error.code in TERMINAL) endSession(error.code)
            return null
        }
    }

    private fun endSession(code: String) {
        tokens.clear()
        events.signOut(reasonFor(code))
    }

    private fun retry(request: Request, tokens: Tokens): Request =
        request.newBuilder().header("Authorization", "Bearer ${tokens.accessToken}").build()

    companion object {
        private val JSON = "application/json".toMediaType()
        private const val MAX_ERROR_BYTES = 16_384L

        /** The refusals after which only signing in again helps. */
        val TERMINAL = setOf("invalid_refresh", "refresh_reused", "session_expired", "session_revoked")

        fun reasonFor(code: String): String = when (code) {
            "refresh_reused" -> "Your session was used somewhere else, so it was ended. Sign in again."
            "session_expired" -> "Your session expired. Sign in again."
            "session_revoked" -> "Your session was ended. Sign in again."
            else -> "Your session is no longer valid. Sign in again."
        }
    }
}
