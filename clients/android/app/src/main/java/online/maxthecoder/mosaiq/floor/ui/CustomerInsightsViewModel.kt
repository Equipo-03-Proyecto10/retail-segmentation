package online.maxthecoder.mosaiq.floor.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.io.IOException
import java.time.Instant
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.CurrentAssignment
import online.maxthecoder.mosaiq.floor.net.MAX_IDS
import online.maxthecoder.mosaiq.floor.net.RecommendationItem
import online.maxthecoder.mosaiq.floor.net.RecommendationReason
import online.maxthecoder.mosaiq.floor.net.RecommendationResult
import retrofit2.Response

/** One recommendation as the panel shows it: the item, its name, its reasons in words. */
data class ShownRecommendation(
    val rank: Int,
    val item: RecommendationItem,
    val name: String,
    val reasons: List<String>
)

/** What the customer panel shows (F17-05). Each part keeps its own error. */
data class InsightsState(
    val customerId: String? = null,
    val loading: Boolean = false,
    val assignment: CurrentAssignment? = null,
    val assignmentError: ApiError? = null,
    val recommendations: RecommendationResult? = null,
    val items: List<ShownRecommendation> = emptyList(),
    val recommendationsError: ApiError? = null,
    val clickNote: String? = null
)

/**
 * The selected customer's segment and recommendations. The segment is shown as
 * segments-service states it, never filled in; recommendations carry ids only,
 * so product names come from one `products?ids=` call per list (ADR-0035). A tap
 * queues one RECO_CLICK and opens the product.
 */
class CustomerInsightsViewModel(
    private val graph: AppGraph
) : ViewModel() {
    val state = MutableStateFlow(InsightsState())
    private var categoryNames: Map<Int, String>? = null

    fun load(customerId: String) {
        state.value = InsightsState(customerId = customerId, loading = true)
        viewModelScope.launch {
            val assignment = call { graph.network.segments.getCurrentAssignment(customerId) }
            val recommendations = call { graph.network.recommendations.getRecommendations(customerId) }
            val result = (recommendations as? Answer.Body)?.value
            val items = result?.items.orEmpty()
            val names = if (items.isEmpty()) emptyMap() else productNames(items.map { it.productId })
            val categories = if (items.isEmpty()) emptyMap() else categories()
            if (state.value.customerId != customerId) return@launch
            state.value =
                InsightsState(
                    customerId = customerId,
                    assignment = (assignment as? Answer.Body)?.value,
                    assignmentError = (assignment as? Answer.Refused)?.error,
                    recommendations = result,
                    items =
                    items.mapIndexed { index, item ->
                        ShownRecommendation(
                            rank = index + 1,
                            item = item,
                            name = names[item.productId] ?: "Product ${item.productId}",
                            reasons = item.reasons.map { describe(it, categories) }
                        )
                    },
                    recommendationsError = (recommendations as? Answer.Refused)?.error
                )
        }
    }

    /**
     * Queues one RECO_CLICK for this tap, with the item's 1-based rank and the
     * assignment's label, then opens the product. Returns false, saying why,
     * when the click cannot be counted (no store checked in, no segment).
     */
    fun open(shown: ShownRecommendation, now: Instant = Instant.now(), openProduct: (Int) -> Unit): Boolean {
        val current = state.value
        val customerId = current.customerId ?: return false
        val label = current.assignment?.labelCode ?: current.recommendations?.labelCode
        val store = graph.session.chosenStore()
        if (store == null || label == null) {
            state.value =
                current.copy(
                    clickNote =
                    if (store == null) {
                        "Check in at your store first, so the click is counted there."
                    } else {
                        "The customer has no segment, so the click cannot be counted."
                    }
                )
            return false
        }
        viewModelScope.launch {
            graph.writes.queueRecoClick(customerId, store.storeId, shown.item.productId, label, shown.rank, now)
        }
        state.value = current.copy(clickNote = null)
        openProduct(shown.item.productId)
        return true
    }

    /** One call for every name in the list; the list is at most the limit (10), never over 50. */
    private suspend fun productNames(ids: List<Int>): Map<Int, String> {
        val answer = call { graph.network.products.listProductsByIds(ids.distinct().take(MAX_IDS).joinToString(",")) }
        return (answer as? Answer.Body)?.value?.products?.associate { it.productId to it.name }.orEmpty()
    }

    private suspend fun categories(): Map<Int, String> {
        categoryNames?.let { return it }
        val answer = call { graph.network.categories.listCategories() }
        val names = (answer as? Answer.Body)?.value?.categories?.associate { it.categoryId to it.name }
        if (names != null) categoryNames = names
        return names.orEmpty()
    }

    private sealed interface Answer<out T> {
        data class Body<T>(val value: T) : Answer<T>

        data class Refused(val error: ApiError) : Answer<Nothing>
    }

    private suspend fun <T> call(request: suspend () -> Response<T>): Answer<T> {
        val response =
            try {
                request()
            } catch (unreachable: IOException) {
                return Answer.Refused(ApiError.unreachable(null))
            }
        val body = response.body()
        return if (response.isSuccessful && body != null) Answer.Body(body) else Answer.Refused(ApiError.from(response))
    }

    companion object {
        /** A reason in plain words. */
        fun describe(reason: RecommendationReason, categories: Map<Int, String>): String {
            val category = reason.categoryId?.let { categories[it] ?: "category ${reason.categoryId}" }
            return when (reason.signal) {
                "segment" -> {
                    val buyers = reason.segmentBuyers ?: 0
                    "$buyers other ${if (buyers == 1) "customer" else "customers"} in the same segment bought it"
                }
                "preferred_category" -> "In $category, a category they like"
                "purchase_history" -> {
                    val percent = Math.round((reason.share ?: 0.0) * PERCENT)
                    "$category makes up $percent% of their purchases"
                }
                else -> reason.signal
            }
        }

        /** Why nothing is recommended, in plain words. */
        fun whyNone(status: String): String = when (status) {
            "none_match" -> "Nothing in stock at their usual store matches what they like, buy or their segment buys."
            "no_open_assignment" -> "No recommendations: the customer has no open segment assignment."
            "unassigned_result" -> "No recommendations: the latest segmentation run left the customer unassigned."
            "no_usual_store" -> "No recommendations: the customer has no purchase in the window, so no usual store."
            else -> "No recommendations ($status)."
        }

        /** The segment state as returned, never filled in. */
        fun segmentLine(assignment: CurrentAssignment): String = when (assignment.state) {
            "assigned" ->
                "Segment ${assignment.labelCode} · R ${assignment.rScore} F ${assignment.fScore} " +
                    "M ${assignment.mScore} · since ${assignment.since}"
            "unassigned_result" ->
                "Unassigned result (run ${assignment.runId}): the latest run found no purchase to score."
            "no_open_assignment" -> "No open assignment: no segmentation run has assigned this customer yet."
            else -> "Segment state: ${assignment.state}"
        }

        private const val PERCENT = 100
    }
}
