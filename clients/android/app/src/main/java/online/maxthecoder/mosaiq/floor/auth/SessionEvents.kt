package online.maxthecoder.mosaiq.floor.auth

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow

/** Why the app signed the user out without being asked. */
data class SignedOut(
    val reason: String
)

/** Tells the screens that the session ended underneath them, e.g. on a refresh. */
class SessionEvents {
    private val events =
        MutableSharedFlow<SignedOut>(extraBufferCapacity = 1, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    val signedOut: SharedFlow<SignedOut> = events

    fun signOut(reason: String) {
        events.tryEmit(SignedOut(reason))
    }
}
