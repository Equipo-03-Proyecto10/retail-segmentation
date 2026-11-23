package online.maxthecoder.mosaiq.floor.ui

import androidx.annotation.StringRes
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
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.net.call

/**
 * A refused promotions list, in the app's own words when the list is not there at all. campaigns serves
 * `listPromotions` now (F25-16), but an instance still on an older release answers 404 with the
 * framework's default text, envelope or not; that text must not reach the floor (#1155). The code and
 * correlation id are kept for "Details". Any other refusal keeps its own message, which [ApiError] has
 * already made plain.
 */
fun promotionsRefusal(error: ApiError, @StringRes missing: Int): ApiError =
    if (error.status == NOT_FOUND) error.copy(message = AppText.get(missing)) else error

private const val NOT_FOUND = 404

/** The promotions the selected customer may be offered here, today (F27-07). */
data class PromotionsState(
    val customerId: String? = null,
    val storeId: Int? = null,
    val loading: Boolean = false,
    val promotions: List<Promotion> = emptyList(),
    val error: ApiError? = null,
    /** Some campaign's arm could not be read, so its offers are held back until a retry answers. */
    val armError: ApiError? = null,
    /** Promotions whose showing the seller recorded for this customer at this store. */
    val recorded: Set<Int> = emptySet(),
    val note: String? = null
)

/**
 * Active promotions for the checked-in store and the in-store channel, read from campaigns-service,
 * minus any campaign whose experiment holds the customer in its control arm: the control is never
 * shown the treatment (ADR-0027, ADR-0068 rule 5). Reading promotions and arms records nothing; an
 * exposure is recorded only when the seller says the offer was shown, as one CAMPAIGN_EXPOSURE.
 */
class PromotionsViewModel(
    private val graph: AppGraph
) : ViewModel() {
    val state = MutableStateFlow(PromotionsState())
    private var channelId: Int? = null

    fun load(customerId: String) {
        val store = graph.session.chosenStore()
        val previous = state.value
        // A refresh for the same customer and store keeps what was already recorded, so it is not recorded twice.
        val same = previous.customerId == customerId && previous.storeId == store?.storeId
        val kept = if (same) previous.recorded else emptySet()
        if (store == null) {
            state.value = PromotionsState(customerId, note = AppText.get(R.string.promotions_no_store))
            return
        }
        state.value = PromotionsState(customerId, store.storeId, loading = true, recorded = kept)
        viewModelScope.launch {
            val loaded = offers(customerId, store.storeId).copy(recorded = kept)
            if (state.value.customerId == customerId && state.value.storeId == store.storeId) state.value = loaded
        }
    }

    /**
     * Records that [promotion] was shown to the customer: one CAMPAIGN_EXPOSURE, queued with its id
     * fixed now. A second tap, or an offer that is not on the list (a control's), records nothing.
     */
    fun record(promotion: Promotion, now: Instant = Instant.now()): Boolean {
        val current = state.value
        val customerId = current.customerId ?: return false
        val storeId = current.storeId ?: return false
        if (promotion !in current.promotions || promotion.promotionId in current.recorded) return false
        state.value = current.copy(recorded = current.recorded + promotion.promotionId)
        viewModelScope.launch { graph.writes.queueCampaignExposure(customerId, storeId, promotion.campaignId, now) }
        return true
    }

    private suspend fun offers(customerId: String, storeId: Int): PromotionsState {
        val base = PromotionsState(customerId, storeId)
        val channel =
            when (val answer = inStoreChannel()) {
                is Answer.Body -> answer.value
                is Answer.Refused -> return base.copy(error = answer.error)
            }
        val active =
            when (val answer = activePromotions(storeId, channel)) {
                is Answer.Body -> answer.value
                is Answer.Refused ->
                    return base.copy(error = promotionsRefusal(answer.error, R.string.promotions_unavailable))
            }
        var armError: ApiError? = null
        val shown =
            active.map { it.campaignId }.distinct().filter { campaignId ->
                when (val arm = call { graph.network.experimentation.getCurrentAssignment(customerId, campaignId) }) {
                    is Answer.Body -> !arm.value.isControl
                    // No arm applies, so no experiment rule does either. An unknown arm might be the
                    // control: its offer is held back rather than risk showing it.
                    is Answer.Refused ->
                        if (arm.error.code == NO_ARM) {
                            true
                        } else {
                            armError = armError ?: arm.error
                            false
                        }
                }
            }.toSet()
        return base.copy(promotions = active.filter { it.campaignId in shown }, armError = armError)
    }

    /** Every page of active promotions that apply to this store and channel, checked again here. */
    private suspend fun activePromotions(storeId: Int, channelId: Int): Answer<List<Promotion>> {
        val found = mutableListOf<Promotion>()
        var page = 1
        do {
            val answer = call { graph.network.campaigns.listPromotions(storeId, channelId, page = page) }
            val body =
                when (answer) {
                    is Answer.Body -> answer.value
                    is Answer.Refused -> return answer
                }
            found += body.promotions
            page++
        } while (body.promotions.isNotEmpty() && found.size < body.pageInfo.total && page <= MAX_PAGES)
        return Answer.Body(found.filter { it.isActive && storeId in it.storeIds && channelId in it.channelIds })
    }

    /** The in-store channel's id, from sales-service, the channel a floor sale is recorded in. */
    private suspend fun inStoreChannel(): Answer<Int> {
        channelId?.let { return Answer.Body(it) }
        return when (val answer = call { graph.network.sales.listChannels() }) {
            is Answer.Body ->
                answer.value.channels.firstOrNull { it.name == PHYSICAL_STORE }?.channelId
                    ?.also { channelId = it }
                    ?.let { Answer.Body(it) }
                    ?: Answer.Refused(
                        ApiError(0, "no_in_store_channel", AppText.get(R.string.promotions_no_channel), null)
                    )
            is Answer.Refused -> answer
        }
    }

    private companion object {
        const val NO_ARM = "assignment_not_found"

        /** The channel a floor sale is recorded in, as SalesViewModel names it. */
        const val PHYSICAL_STORE = "physical_store"

        /** At most this many full pages: a store never runs a thousand promotions at once. */
        const val MAX_PAGES = 10
    }
}
