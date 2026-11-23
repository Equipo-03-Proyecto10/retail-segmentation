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
    val queuedRowId: Long? = null,
    /** The refused sales' client ids that the cart being built corrects (F27-13). */
    val corrects: List<String> = emptyList()
)

/**
 * The cart a confirmed sale was made from, kept by its client id. A queued sale's body holds product ids,
 * quantities and prices but no product name or list price, so this is what lets a refused sale be corrected
 * after "Start another sale" or a restart without reading the catalog again. [corrects] lists the refused
 * sales this one replaces: when this sale is recorded, they are done with and leave the list (F27-13).
 */
@Serializable
data class KeptCart(
    val customer: Customer? = null,
    val lines: List<DraftLine> = emptyList(),
    val corrects: List<String> = emptyList()
)

/** Where the sale being built survives process death; one draft per user. */
interface SaleDraftStore {
    fun load(userId: String): SaleDraft?

    fun save(userId: String, draft: SaleDraft)

    /** Keeps the cart [clientId] was confirmed from, written before the sale is queued. */
    fun keep(userId: String, clientId: String, cart: KeptCart)

    fun kept(userId: String, clientId: String): KeptCart?

    /** The client ids this user has a cart kept for. */
    fun keptIds(userId: String): Set<String>

    fun forget(userId: String, clientIds: Collection<String>)

    /** Forgets every user's draft and kept carts, as signing out does. */
    fun clear()
}

class InMemorySaleDraftStore : SaleDraftStore {
    private val drafts = mutableMapOf<String, SaleDraft>()
    private val carts = mutableMapOf<Pair<String, String>, KeptCart>()

    @Synchronized
    override fun load(userId: String): SaleDraft? = drafts[userId]

    @Synchronized
    override fun save(userId: String, draft: SaleDraft) {
        drafts[userId] = draft
    }

    @Synchronized
    override fun keep(userId: String, clientId: String, cart: KeptCart) {
        carts[userId to clientId] = cart
    }

    @Synchronized
    override fun kept(userId: String, clientId: String): KeptCart? = carts[userId to clientId]

    @Synchronized
    override fun keptIds(userId: String): Set<String> =
        carts.keys.filter { it.first == userId }.map { it.second }.toSet()

    @Synchronized
    override fun forget(userId: String, clientIds: Collection<String>) {
        clientIds.forEach { carts.remove(userId to it) }
    }

    @Synchronized
    override fun clear() {
        drafts.clear()
        carts.clear()
    }
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

    override fun keep(userId: String, clientId: String, cart: KeptCart) {
        prefs.edit(commit = true) {
            putString(keptKey(userId, clientId), json.encodeToString(KeptCart.serializer(), cart))
        }
    }

    override fun kept(userId: String, clientId: String): KeptCart? =
        prefs.getString(keptKey(userId, clientId), null)?.let {
            runCatching { json.decodeFromString(KeptCart.serializer(), it) }.getOrNull()
        }

    override fun keptIds(userId: String): Set<String> {
        val prefix = KEPT_PREFIX + userId + SEPARATOR
        return prefs.all.keys.filter { it.startsWith(prefix) }.map { it.removePrefix(prefix) }.toSet()
    }

    override fun forget(userId: String, clientIds: Collection<String>) {
        if (clientIds.isEmpty()) return
        prefs.edit(commit = true) { clientIds.forEach { remove(keptKey(userId, it)) } }
    }

    override fun clear() {
        prefs.edit(commit = true) { clear() }
    }

    private fun keptKey(userId: String, clientId: String) = KEPT_PREFIX + userId + SEPARATOR + clientId

    private companion object {
        const val FILE = "floor.sale-draft"
        const val KEY_PREFIX = "draft."
        const val KEPT_PREFIX = "kept."
        const val SEPARATOR = "/"
    }
}
