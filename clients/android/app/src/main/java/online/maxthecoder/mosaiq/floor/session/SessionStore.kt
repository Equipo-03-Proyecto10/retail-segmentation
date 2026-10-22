package online.maxthecoder.mosaiq.floor.session

import android.content.Context
import androidx.core.content.edit
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** Who signed in, kept so the floor stays usable while the network is down. */
data class Profile(
    val userId: String,
    val name: String,
    val role: String
)

/** The store the user picked, which availability, engagement and sales use. */
data class ChosenStore(
    val storeId: Int,
    val name: String
)

/**
 * Who is signed in and which store is theirs for this session. Nothing secret:
 * the tokens live in the Keystore-encrypted store. The floor-write queue reads
 * the user id to send a row only under the session of the user who made it.
 */
interface SessionStore {
    val userId: StateFlow<String?>

    fun signedIn(profile: Profile)

    /** The last profile users-service returned for the signed-in user. */
    fun profile(): Profile?

    /** Forgets the user and their store; queued rows keep their own user id. */
    fun signedOut()

    fun chosenStore(): ChosenStore?

    fun choose(store: ChosenStore)
}

class InMemorySessionStore(
    userId: String? = null
) : SessionStore {
    private val current = MutableStateFlow(userId)
    private var store: ChosenStore? = null
    private var cached: Profile? = null

    override val userId: StateFlow<String?> = current

    override fun signedIn(profile: Profile) {
        cached = profile
        current.value = profile.userId
    }

    override fun profile(): Profile? = cached?.takeIf { it.userId == current.value }

    override fun signedOut() {
        current.value = null
        cached = null
        store = null
    }

    override fun chosenStore(): ChosenStore? = store

    override fun choose(store: ChosenStore) {
        this.store = store
    }
}

/** The device's copy, so a restart remembers whose rows may be sent. */
class PrefsSessionStore(
    context: Context
) : SessionStore {
    private val prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
    private val current = MutableStateFlow(prefs.getString(USER_ID, null))

    override val userId: StateFlow<String?> = current

    override fun signedIn(profile: Profile) {
        prefs.edit {
            putString(USER_ID, profile.userId)
            putString(NAME, profile.name)
            putString(ROLE, profile.role)
        }
        current.value = profile.userId
    }

    override fun profile(): Profile? {
        val id = prefs.getString(USER_ID, null) ?: return null
        val name = prefs.getString(NAME, null) ?: return null
        val role = prefs.getString(ROLE, null) ?: return null
        return Profile(id, name, role)
    }

    override fun signedOut() {
        prefs.edit { clear() }
        current.value = null
    }

    override fun chosenStore(): ChosenStore? {
        val id = prefs.getInt(STORE_ID, -1)
        val name = prefs.getString(STORE_NAME, null)
        return if (id > 0 && name != null) ChosenStore(id, name) else null
    }

    override fun choose(store: ChosenStore) {
        prefs.edit {
            putInt(STORE_ID, store.storeId)
            putString(STORE_NAME, store.name)
        }
    }

    private companion object {
        const val FILE = "floor.session"
        const val USER_ID = "user_id"
        const val NAME = "name"
        const val ROLE = "role"
        const val STORE_ID = "store_id"
        const val STORE_NAME = "store_name"
    }
}
