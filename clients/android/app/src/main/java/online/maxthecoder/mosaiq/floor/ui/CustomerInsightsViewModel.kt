package online.maxthecoder.mosaiq.floor.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.time.Instant
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.AppText
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.CurrentAssignment
import online.maxthecoder.mosaiq.floor.net.MAX_IDS
import online.maxthecoder.mosaiq.floor.net.RecommendationItem
import online.maxthecoder.mosaiq.floor.net.RecommendationReason
import online.maxthecoder.mosaiq.floor.net.RecommendationResult
import online.maxthecoder.mosaiq.floor.net.call

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
            val namesAnswer =
                if (items.isEmpty()) Answer.Body(emptyMap()) else productNames(items.map { it.productId })
            val categoriesAnswer = if (items.isEmpty()) Answer.Body(emptyMap()) else categories()
            val dependencyError =
                listOf(namesAnswer, categoriesAnswer)
                    .filterIsInstance<Answer.Refused>()
                    .firstOrNull()
                    ?.error
            val names = (namesAnswer as? Answer.Body)?.value.orEmpty()
            val categoryNames = (categoriesAnswer as? Answer.Body)?.value.orEmpty()
            if (state.value.customerId != customerId) return@launch
            state.value =
                InsightsState(
                    customerId = customerId,
                    assignment = (assignment as? Answer.Body)?.value,
                    assignmentError = (assignment as? Answer.Refused)?.error,
                    recommendations = result,
                    items =
                    if (dependencyError == null) {
                        items.mapIndexed { index, item ->
                            ShownRecommendation(
                                rank = index + 1,
                                item = item,
                                name =
                                names[item.productId]
                                    ?: AppText.get(R.string.insights_product_fallback, item.productId),
                                reasons = item.reasons.map { describe(it, categoryNames) }
                            )
                        }
                    } else {
                        emptyList()
                    },
                    recommendationsError = (recommendations as? Answer.Refused)?.error ?: dependencyError
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
                        AppText.get(R.string.insights_click_no_store)
                    } else {
                        AppText.get(R.string.insights_click_no_segment)
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
    private suspend fun productNames(ids: List<Int>): Answer<Map<Int, String>> {
        val answer = call { graph.network.products.listProductsByIds(ids.distinct().take(MAX_IDS).joinToString(",")) }
        return when (answer) {
            is Answer.Body -> Answer.Body(answer.value.products.associate { it.productId to it.name })
            is Answer.Refused -> answer
        }
    }

    private suspend fun categories(): Answer<Map<Int, String>> {
        categoryNames?.let { return Answer.Body(it) }
        val answer = call { graph.network.categories.listCategories() }
        return when (answer) {
            is Answer.Body -> {
                val names = answer.value.categories.associate { it.categoryId to it.name }
                categoryNames = names
                Answer.Body(names)
            }
            is Answer.Refused -> answer
        }
    }

    companion object {
        /** A reason in plain words. */
        fun describe(reason: RecommendationReason, categories: Map<Int, String>): String {
            val category = reason.categoryId?.let {
                categories[it] ?: AppText.get(R.string.insights_category_fallback, reason.categoryId)
            }
            return when (reason.signal) {
                "segment" -> {
                    val buyers = reason.segmentBuyers ?: 0
                    AppText.plural(R.plurals.insights_reason_segment, buyers, buyers)
                }
                "preferred_category" -> AppText.get(R.string.insights_reason_category, category.orEmpty())
                "purchase_history" -> {
                    val percent = Math.round((reason.share ?: 0.0) * PERCENT)
                    AppText.get(R.string.insights_reason_history, category.orEmpty(), percent)
                }
                else -> reason.signal
            }
        }

        /** Why nothing is recommended, in plain words. */
        fun whyNone(status: String): String = when (status) {
            "none_match" -> AppText.get(R.string.insights_none_match)
            "no_open_assignment" -> AppText.get(R.string.insights_none_no_assignment)
            "unassigned_result" -> AppText.get(R.string.insights_none_unassigned)
            "no_usual_store" -> AppText.get(R.string.insights_none_no_store)
            else -> AppText.get(R.string.insights_none_other, status)
        }

        /** The segment state as returned, never filled in. */
        fun segmentLine(assignment: CurrentAssignment): String = when (assignment.state) {
            "assigned" ->
                AppText.get(
                    R.string.insights_line_assigned,
                    assignment.labelCode.toString(),
                    assignment.rScore.toString(),
                    assignment.fScore.toString(),
                    assignment.mScore.toString(),
                    assignment.since.toString()
                )
            "unassigned_result" ->
                AppText.get(R.string.insights_line_unassigned, assignment.runId.toString())
            "no_open_assignment" -> AppText.get(R.string.insights_line_none)
            else -> AppText.get(R.string.insights_line_other, assignment.state)
        }

        private const val PERCENT = 100
    }
}
