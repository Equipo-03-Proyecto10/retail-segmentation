package online.maxthecoder.mosaiq.floor.ui

import java.time.Instant
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.AppText
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.auth.FloorAccess
import online.maxthecoder.mosaiq.floor.auth.FloorSection
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.MAX_IDS
import online.maxthecoder.mosaiq.floor.net.RecommendationItemV2
import online.maxthecoder.mosaiq.floor.net.RecommendationReason
import online.maxthecoder.mosaiq.floor.net.RecommendationV2
import online.maxthecoder.mosaiq.floor.net.call

/** The three kinds of a `/v2` item (contracts/recommendations). */
const val KIND_PRIMARY = "primary"
const val KIND_ALTERNATIVE = "alternative"
const val KIND_ELSEWHERE = "elsewhere"

/**
 * One item as the seller reads it: its name, where it can be had, and why it is there, each
 * in plain words. [alternative] and [elsewhere] mark an item that is not a recommendation of
 * its own: it stands in for another product, or it is held only by another store.
 */
data class ExplainedItem(
    val item: RecommendationItemV2,
    val name: String,
    val availability: String,
    val note: String?,
    val reasons: List<String>
) {
    val rank: Int get() = item.position
    val alternative: Boolean get() = item.kind == KIND_ALTERNATIVE
    val elsewhere: Boolean get() = item.kind == KIND_ELSEWHERE
}

/** What the customer's recommendations part shows (F27-06). */
data class ExplainedState(
    val customerId: String? = null,
    val loading: Boolean = false,
    /** No store is checked in, so nothing was asked: availability is judged at a store. */
    val noStore: Boolean = false,
    val result: RecommendationV2? = null,
    val items: List<ExplainedItem> = emptyList(),
    val error: ApiError? = null
) {
    /** The service's reason when nothing is offered, in plain words. */
    val whyNone: String?
        get() = result?.takeIf { items.isEmpty() }?.let { ExplainedRecommendations.reasonFor(it.status) }
}

/**
 * The customer's recommendations judged at the checked-in store (F27-06), and what was
 * really shown of them. An item counts as shown when the screen says it is on screen
 * ([shown]); each is queued as one `RECO_IMPRESSION` per decision and position, however
 * often it is composed, scrolled back to or the screen rotated, because the record of what
 * was queued lives here and not in the composition. A tap is a separate `RECO_CLICK`, and opens the product. Only a
 * role that records the Customer section's engagement queues either (FloorAccess.records).
 */
class ExplainedRecommendations(
    private val graph: AppGraph,
    private val scope: CoroutineScope
) {
    val state = MutableStateFlow(ExplainedState())
    private val impressed = mutableSetOf<String>()
    private var job: Job? = null
    private var categoryNames: Map<Int, String>? = null
    private var storeNames: Map<Int, String>? = null

    /** Asks for [customerId]'s recommendations at the checked-in store. */
    fun load(customerId: String) {
        val store = graph.session.chosenStore()
        job?.cancel()
        if (store == null) {
            state.value = ExplainedState(customerId = customerId, noStore = true)
            return
        }
        state.value = ExplainedState(customerId = customerId, loading = true)
        job =
            scope.launch {
                val answer = call { graph.network.recommendations.getRecommendationsV2(customerId, store.storeId) }
                val result = (answer as? Answer.Body)?.value
                val explained = result?.let { explain(it) }
                if (state.value.customerId != customerId) return@launch
                state.value =
                    ExplainedState(
                        customerId = customerId,
                        result = result,
                        items = (explained as? Answer.Body)?.value.orEmpty(),
                        error = (answer as? Answer.Refused)?.error ?: (explained as? Answer.Refused)?.error
                    )
            }
    }

    /** Forgets the customer, and what was shown, when the signed-in user changes. */
    fun clear() {
        job?.cancel()
        impressed.clear()
        state.value = ExplainedState()
    }

    /**
     * [shown] is on screen. Queues its one `RECO_IMPRESSION`; any later call for the same
     * decision and position queues nothing. Returns whether one was queued.
     */
    fun shown(shown: ExplainedItem, now: Instant = Instant.now()): Boolean {
        val context = recording() ?: return false
        val key = context.result.decisionId + "#" + shown.rank
        if (!impressed.add(key)) return false
        scope.launch {
            graph.writes.queueRecoImpression(
                context.customerId,
                context.storeId,
                shown.item.productId,
                context.label,
                shown.rank,
                now
            )
        }
        return true
    }

    /**
     * A tap: one `RECO_CLICK` per tap, separate from the item's impression, then the product
     * opens. Returns false, opening nothing, when the tap cannot be counted.
     */
    fun tapped(shown: ExplainedItem, now: Instant = Instant.now(), openProduct: (Int) -> Unit): Boolean {
        val context = recording() ?: return false
        scope.launch {
            graph.writes.queueRecoClick(
                context.customerId,
                context.storeId,
                shown.item.productId,
                context.label,
                shown.rank,
                now
            )
        }
        openProduct(shown.item.productId)
        return true
    }

    /** Whether the signed-in role records what it is shown and taps here. */
    fun records(): Boolean = FloorAccess.records(graph.session.profile()?.role, FloorSection.CUSTOMER)

    private class Recording(val customerId: String, val storeId: Int, val label: String, val result: RecommendationV2)

    private fun recording(): Recording? {
        if (!records()) return null
        val current = state.value
        val result = current.result ?: return null
        val label = result.labelCode ?: return null
        val storeId = result.storeId ?: return null
        return Recording(current.customerId ?: return null, storeId, label, result)
    }

    /** Names for the ids the answer carries, from their owners (ADR-0035), then the words. */
    private suspend fun explain(result: RecommendationV2): Answer<List<ExplainedItem>> {
        if (result.items.isEmpty()) return Answer.Body(emptyList())
        val ids = (result.items.map { it.productId } + result.items.mapNotNull { it.alternativeFor }).distinct()
        val names =
            when (val answer = call { graph.network.products.listProductsByIds(ids.take(MAX_IDS).joinToString(",")) }) {
                is Answer.Body -> answer.value.products.associate { it.productId to it.name }
                is Answer.Refused -> return answer
            }
        val categories = categories()
        val others = result.items.filter { it.kind == KIND_ELSEWHERE }.map { it.availability.storeId }
        val stores = if (others.isEmpty()) emptyMap() else stores()
        fun name(id: Int) = names[id] ?: AppText.get(R.string.insights_product_fallback, id)
        return Answer.Body(
            result.items.map { item ->
                val unavailable = item.unavailableReason?.let(::unavailable).orEmpty()
                ExplainedItem(
                    item = item,
                    name = name(item.productId),
                    availability =
                    if (item.kind == KIND_ELSEWHERE) {
                        val store =
                            stores[item.availability.storeId]
                                ?: AppText.get(R.string.reco_store_fallback, item.availability.storeId)
                        AppText.plural(
                            R.plurals.reco_on_hand_at,
                            item.availability.quantityOnHand,
                            item.availability.quantityOnHand,
                            store
                        )
                    } else {
                        AppText.plural(
                            R.plurals.reco_on_hand_here,
                            item.availability.quantityOnHand,
                            item.availability.quantityOnHand
                        )
                    },
                    note =
                    when (item.kind) {
                        KIND_ALTERNATIVE ->
                            AppText.get(R.string.reco_alternative_for, name(item.alternativeFor ?: 0), unavailable)
                        KIND_ELSEWHERE -> AppText.get(R.string.reco_elsewhere_why, unavailable)
                        else -> null
                    },
                    reasons =
                    item.reasons.map { reason ->
                        describe(reason, categories, item.alternativeFor?.let(::name).orEmpty())
                    }
                )
            }
        )
    }

    private suspend fun categories(): Map<Int, String> {
        categoryNames?.let { return it }
        val answer = call { graph.network.categories.listCategories() }
        return (answer as? Answer.Body)?.value?.categories.orEmpty().associate { it.categoryId to it.name }
            .also { if (answer is Answer.Body) categoryNames = it }
    }

    private suspend fun stores(): Map<Int, String> {
        storeNames?.let { return it }
        val answer = call { graph.network.stores.listStores(size = STORE_PAGE) }
        return (answer as? Answer.Body)?.value?.stores.orEmpty().associate { it.storeId to it.name }
            .also { if (answer is Answer.Body) storeNames = it }
    }

    companion object {
        private const val STORE_PAGE = 100

        /** A signal in words: `/v1`'s three, and the one an alternative carries. */
        fun describe(reason: RecommendationReason, categories: Map<Int, String>, replaced: String): String =
            if (reason.signal == "relation_substitute") {
                AppText.get(R.string.reco_reason_substitute, replaced)
            } else {
                CustomerInsightsViewModel.describe(reason, categories)
            }

        /** Why a product is not available at the reference store, as a phrase. */
        fun unavailable(reason: String): String = when (reason) {
            "out_of_stock" -> AppText.get(R.string.reco_unavailable_out_of_stock)
            "not_sellable" -> AppText.get(R.string.reco_unavailable_not_sellable)
            "channel_not_enabled" -> AppText.get(R.string.reco_unavailable_channel_not_enabled)
            else -> reason
        }

        /** The service's status, when nothing is offered, in plain words. */
        fun reasonFor(status: String): String = when (status) {
            "none_match" -> AppText.get(R.string.reco_none_match)
            "PERSONALIZATION_WITHDRAWN" -> AppText.get(R.string.reco_none_withdrawn)
            else -> CustomerInsightsViewModel.whyNone(status)
        }
    }
}
