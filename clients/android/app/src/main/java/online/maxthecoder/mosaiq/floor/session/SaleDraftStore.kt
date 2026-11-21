package online.maxthecoder.mosaiq.floor.session

import android.content.Context
import androidx.core.content.edit
import kotlinx.serialization.Serializable
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.net.json

/** One cart line as it is kept: the product as it was listed, its quantity, and its promotion. */
@Serializable
data class DraftLine(
    val product: Product,
    val quantity: Int,
    val promotion: Promotion? = null
)

/**
 * The sale being built, kept on the device so a killed process comes back to it (F27-05). [confirmKey]
 * is the client id generated at the confirming tap; it is written here before the sale is queued, so
 * after a restart the queued row is found by it, and a tap that never reached the queue is known too.
 */
@Serializable
data class SaleDraft(
    val customer: Customer? = null,
    val lines: List<DraftLine> = emptyList(),
    val confirmKey: String? = null,
    val queuedRowId: Long? = null
)

/** Where the sale being built survives process death; one draft per user. */
interface SaleDraftStore {
    fun load(userId: String): SaleDraft?

    fun save(userId: String, draft: SaleDraft)

    /** Forgets every user's draft, as signing out does. */
    fun clear()
}

class InMemorySaleDraftStore : SaleDraftStore {
    private val drafts = mutableMapOf<String, SaleDraft>()

    @Synchronized
    override fun load(userId: String): SaleDraft? = drafts[userId]

    @Synchronized
    override fun save(userId: String, draft: SaleDraft) {
        drafts[userId] = draft
    }

    @Synchronized
    override fun clear() = drafts.clear()
}

/**
 * The device's copy. Written synchronously: the confirming tap's client id must be on disk before
 * the sale is queued, or a process death between the two could not be told apart from no tap.
 */
class PrefsSaleDraftStore(
    context: Context
) : SaleDraftStore {
    private val prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    override fun load(userId: String): SaleDraft? = prefs.getString(KEY_PREFIX + userId, null)?.let {
        runCatching { json.decodeFromString(SaleDraft.serializer(), it) }.getOrNull()
    }

    override fun save(userId: String, draft: SaleDraft) {
        prefs.edit(commit = true) { putString(KEY_PREFIX + userId, json.encodeToString(SaleDraft.serializer(), draft)) }
    }

    override fun clear() {
        prefs.edit(commit = true) { clear() }
    }

    private companion object {
        const val FILE = "floor.sale-draft"
        const val KEY_PREFIX = "draft."
    }
}
