package online.maxthecoder.mosaiq.floor.auth

import java.io.IOException
import online.maxthecoder.mosaiq.floor.AppText
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.CurrentUser
import online.maxthecoder.mosaiq.floor.net.Network
import online.maxthecoder.mosaiq.floor.net.SignInRequest
import online.maxthecoder.mosaiq.floor.session.InMemorySessionStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.session.SessionStore
import retrofit2.Response

/** What a sign-in or a profile read came to. */
sealed interface SessionResult {
    data class SignedIn(
        val user: CurrentUser
    ) : SessionResult

    /** A role the floor app is not for: the session was ended at once. */
    data class RoleRefused(
        val role: String
    ) : SessionResult

    data class Failed(
        val error: ApiError
    ) : SessionResult
}

/**
 * Sign-in, the caller's profile and sign-out, over auth-service and
 * users-service. The role gate is for usability only: every service refuses
 * other roles anyway, through its contract's `x-mosaiq-roles`.
 */
class SessionRepository(
    private val network: Network,
    private val tokens: TokenStore,
    private val deviceLabel: String,
    private val session: SessionStore = InMemorySessionStore(),
    /** Told which user signed out, so their notifications and poll end with them (ADR-0071). */
    private val onSignedOut: (String?) -> Unit = {}
) {
    val isSignedIn: Boolean get() = tokens.read() != null

    suspend fun signIn(email: String, password: String): SessionResult {
        val response =
            call { network.auth.createSession(SignInRequest(email.trim(), password, deviceLabel)) }
                ?: return SessionResult.Failed(ApiError.unreachable(null))
        val result = response.body()
        if (response.code() != 201 || result == null) return SessionResult.Failed(friendly(ApiError.from(response)))
        tokens.write(Tokens(result.accessToken, result.refreshToken))
        return currentUser()
    }

    /** `GET /v1/users/me`, then the floor-role gate. */
    suspend fun currentUser(): SessionResult {
        val response =
            call { network.users.getCurrentUser() }
                ?: return offline()
        val user = response.body()
        if (!response.isSuccessful || user == null) {
            val error = ApiError.from(response)
            // A 401 after a refresh that could not run is the refresh's story.
            val shown = if (error.status == 401) network.authenticator.lastFailure ?: error else error
            if (tokens.read() == null || shown.code in SIGNED_OUT) signOutLocally()
            return SessionResult.Failed(friendly(shown))
        }
        if (user.roleCode !in FLOOR_ROLES) {
            signOut()
            return SessionResult.RoleRefused(user.roleCode)
        }
        // The floor-write queue sends a row only under this user's session.
        session.signedIn(Profile(user.userId, user.name, user.roleCode))
        return SessionResult.SignedIn(user)
    }

    /**
     * No answer from users-service. With a session on this phone, the floor
     * stays open as the user it last confirmed, so queued writes and the outbox
     * stay reachable offline; the services check every request anyway.
     */
    private suspend fun offline(): SessionResult {
        val known = session.profile()
        if (tokens.read() == null || known == null) return SessionResult.Failed(ApiError.unreachable(null))
        // A role this build does not serve, kept from an older build, opens nothing either.
        if (known.role !in FLOOR_ROLES) {
            signOutLocally()
            return SessionResult.RoleRefused(known.role)
        }
        return SessionResult.SignedIn(CurrentUser(known.userId, known.name, known.role))
    }

    /** Forgets the session here without asking auth-service: for when the network is known to be down. */
    fun signOutLocally() {
        val userId = session.userId.value
        tokens.clear()
        session.signedOut()
        onSignedOut(userId)
    }

    /** Ends the session at auth-service, then forgets it here whatever the answer. */
    suspend fun signOut() {
        if (tokens.read() != null) call { network.auth.deleteCurrentSession() }
        signOutLocally()
    }

    private fun friendly(error: ApiError): ApiError = when {
        error.code == "rate_limited" && error.retryAfterSeconds != null ->
            error.copy(
                message = AppText.plural(
                    R.plurals.auth_rate_limited,
                    error.retryAfterSeconds.coerceIn(0, Int.MAX_VALUE.toLong()).toInt(),
                    error.retryAfterSeconds
                )
            )
        error.code == "auth_unavailable" ->
            error.copy(message = AppText.get(R.string.auth_unavailable))
        error.code == "database_unavailable" ->
            error.copy(message = AppText.get(R.string.auth_service_unavailable))
        else -> error
    }

    private suspend fun <T> call(block: suspend () -> Response<T>): Response<T>? = try {
        block()
    } catch (unreachable: IOException) {
        null
    }

    companion object {
        /**
         * ADR-0060: the four store profiles, one role each, and the administrator, who is not
         * confined to stores. Any other code is turned away (ADR-0076). `FloorAccess.profiles`
         * gives each of these its navigation, and a test keeps the two in step.
         */
        val FLOOR_ROLES =
            setOf("SALES_ASSOCIATE", "STORE_MANAGER", "LOYALTY_OFFICER", "COMMERCIAL_SUPERVISOR", "ADMIN")

        private val SIGNED_OUT = setOf("invalid_refresh", "refresh_reused", "session_expired", "session_revoked")
    }
}
