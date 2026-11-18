package online.maxthecoder.mosaiq.floor.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.FloorCurrentAssignment
import online.maxthecoder.mosaiq.floor.net.FloorCustomer
import online.maxthecoder.mosaiq.floor.net.InterestProfile
import online.maxthecoder.mosaiq.floor.net.LookupLoyaltyCardRequest
import online.maxthecoder.mosaiq.floor.net.RestrictionProfile
import online.maxthecoder.mosaiq.floor.net.call
import online.maxthecoder.mosaiq.floor.net.totalPages

/** What the last loyalty QR read came to. None of these holds the code. */
sealed interface CardRead {
    data object Idle : CardRead

    data object Looking : CardRead

    /** The QR holds something other than a loyalty code; nothing was sent. */
    data object NotACard : CardRead

    data object Unknown : CardRead

    data object Revoked : CardRead

    data class Failed(val error: ApiError) : CardRead
}

data class FloorSearchState(
    val query: String = "",
    val page: Int = 1,
    val totalPages: Int = 1,
    val customers: List<FloorCustomer> = emptyList(),
    val searched: Boolean = false,
    val loading: Boolean = false,
    val error: ApiError? = null
)

/**
 * The customer's permitted commercial profile (F27-03): their segment, interests and
 * restrictions, each read from the service that owns it in the representation it gives this
 * role (ADR-0060), and each with its own error. Every model here declares only the elements
 * a floor role may read, so an excluded one is never kept, and nothing is filled in.
 */
data class CommercialProfile(
    val segment: FloorCurrentAssignment? = null,
    val segmentError: ApiError? = null,
    val interests: InterestProfile? = null,
    val interestsError: ApiError? = null,
    val restrictions: RestrictionProfile? = null,
    val restrictionsError: ApiError? = null,
    val categoryNames: Map<Int, String> = emptyMap(),
    val channelNames: Map<Int, String> = emptyMap()
) {
    /**
     * Whether anything may be offered as tailored to this customer: only when their
     * restrictions were read and say personalization is allowed. An objection, or
     * restrictions that could not be read, means nothing is personalized.
     */
    val personalized: Boolean get() = restrictions?.personalizationAllowed == true
}

/** The customer in the caller's floor view; [byCard] when a loyalty card identified them. */
data class FloorProfileState(
    val customerId: String? = null,
    val customer: FloorCustomer? = null,
    val byCard: Boolean = false,
    val loading: Boolean = false,
    val error: ApiError? = null,
    val commercial: CommercialProfile = CommercialProfile()
)

/** A loyalty code as `CardCode` in contracts/customers: 26 Crockford base32 characters. */
private val CARD_CODE = Regex("^[0-9A-HJKMNP-TV-Z]{26}$")

fun isLoyaltyCode(raw: String): Boolean = CARD_CODE.matches(raw)

/** At least this many characters, once trimmed, before `customers/floor-search` accepts `q`. */
const val FLOOR_SEARCH_MIN = 3

/**
 * Identifies the customer being served (F27-02): by the loyalty QR they show, or by a
 * floor search when there is no card or no camera. The scanned code lives only for the
 * one lookup request that carries it in its body: it is never kept in state, saved state,
 * the outbox or a log, so it is never written to the device.
 */
class IdentifyViewModel(
    private val graph: AppGraph
) : ViewModel() {
    val card = MutableStateFlow<CardRead>(CardRead.Idle)
    val search = MutableStateFlow(FloorSearchState())
    val profile = MutableStateFlow(FloorProfileState())
    private var owner = graph.session.userId.value
    private var searchJob: Job? = null
    private var profileJob: Job? = null
    private var categoryNames: Map<Int, String>? = null
    private var channelNames: Map<Int, String>? = null

    init {
        viewModelScope.launch {
            graph.session.userId.collect { user ->
                if (user != owner) {
                    owner = user
                    searchJob?.cancel()
                    profileJob?.cancel()
                    card.value = CardRead.Idle
                    search.value = FloorSearchState()
                    profile.value = FloorProfileState()
                }
            }
        }
    }

    private fun stillOwner(user: String?): Boolean = user != null && graph.session.userId.value == user

    /** A decoded QR. A found card opens the profile through [onFound] with the customer id only. */
    fun onDecoded(raw: String, onFound: (String) -> Unit) {
        if (card.value == CardRead.Looking) return
        if (!isLoyaltyCode(raw.trim())) {
            card.value = CardRead.NotACard
            return
        }
        val user = graph.session.userId.value ?: return
        card.value = CardRead.Looking
        viewModelScope.launch {
            val answer = call { graph.network.customers.lookupLoyaltyCard(LookupLoyaltyCardRequest(raw.trim())) }
            if (!stillOwner(user)) return@launch
            card.value =
                when (answer) {
                    is Answer.Body -> {
                        open(answer.value.customerId, byCard = true)
                        onFound(answer.value.customerId)
                        CardRead.Idle
                    }
                    is Answer.Refused -> refusal(answer.error)
                }
        }
    }

    /** Ready for the next read. */
    fun scanAgain() {
        card.value = CardRead.Idle
    }

    /** The contract's floor search: never under its minimum query, one page of at most 20. */
    fun search(query: String = search.value.query, page: Int = 1) {
        val q = query.trim()
        if (q.length < FLOOR_SEARCH_MIN) return
        val user = graph.session.userId.value ?: return
        search.value = search.value.copy(query = q, page = page, searched = true, loading = true, error = null)
        searchJob?.cancel()
        searchJob =
            viewModelScope.launch {
                val answer = call { graph.network.customers.searchFloorCustomers(q, page) }
                if (!stillOwner(user)) return@launch
                search.value =
                    when (answer) {
                        is Answer.Body ->
                            search.value.copy(
                                loading = false,
                                customers = answer.value.customers,
                                totalPages = totalPages(answer.value.pageInfo.total, answer.value.pageInfo.size)
                            )
                        is Answer.Refused ->
                            search.value.copy(loading = false, customers = emptyList(), error = answer.error)
                    }
            }
    }

    /** Reads the customer in this role's floor view, unless it is already shown or loading. */
    fun open(customerId: String, byCard: Boolean = false) {
        val current = profile.value
        val held = current.loading || current.customer != null
        if (current.customerId == customerId && current.error == null && held) {
            if (byCard && !current.byCard) profile.value = current.copy(byCard = true)
            return
        }
        val user = graph.session.userId.value ?: return
        profile.value = FloorProfileState(customerId = customerId, byCard = byCard, loading = true)
        profileJob?.cancel()
        profileJob =
            viewModelScope.launch {
                val answer = call { graph.network.customers.getFloorCustomer(customerId) }
                if (!stillOwner(user)) return@launch
                if (answer is Answer.Refused) {
                    profile.value = profile.value.copy(loading = false, error = answer.error)
                    return@launch
                }
                val commercial = commercialProfile(customerId)
                if (!stillOwner(user)) return@launch
                profile.value =
                    profile.value.copy(
                        loading = false,
                        customer = (answer as Answer.Body).value,
                        commercial = commercial
                    )
            }
    }

    /** The three owners' reads, side by side, then the names of the ids they returned. */
    private suspend fun commercialProfile(customerId: String): CommercialProfile {
        val (segment, interests, restrictions) =
            coroutineScope {
                val segment = async { call { graph.network.segments.getFloorCurrentAssignment(customerId) } }
                val interests = async { call { graph.network.preferences.getInterestProfile(customerId) } }
                val restrictions = async { call { graph.network.preferences.getRestrictionProfile(customerId) } }
                Triple(segment.await(), interests.await(), restrictions.await())
            }
        val interestBody = (interests as? Answer.Body)?.value
        val restrictionBody = (restrictions as? Answer.Body)?.value
        val categoryIds =
            interestBody?.declaredCategoryIds.orEmpty() +
                interestBody?.inferredInterests.orEmpty().map { it.categoryId } +
                restrictionBody?.restrictedCategoryIds.orEmpty()
        return CommercialProfile(
            segment = (segment as? Answer.Body)?.value,
            segmentError = (segment as? Answer.Refused)?.error,
            interests = interestBody,
            interestsError = (interests as? Answer.Refused)?.error,
            restrictions = restrictionBody,
            restrictionsError = (restrictions as? Answer.Refused)?.error,
            categoryNames = if (categoryIds.isEmpty()) emptyMap() else categoryNames(),
            channelNames = if (restrictionBody?.restrictedChannelIds.isNullOrEmpty()) emptyMap() else channelNames()
        )
    }

    /** Category names for the ids above; a refusal leaves them unnamed, never hides them. */
    private suspend fun categoryNames(): Map<Int, String> {
        categoryNames?.let { return it }
        val answer = call { graph.network.categories.listCategories() }
        return (answer as? Answer.Body)?.value?.categories.orEmpty().associate { it.categoryId to it.name }
            .also { if (answer is Answer.Body) categoryNames = it }
    }

    private suspend fun channelNames(): Map<Int, String> {
        channelNames?.let { return it }
        val answer = call { graph.network.sales.listChannels() }
        return (answer as? Answer.Body)?.value?.channels.orEmpty().associate { it.channelId to it.name }
            .also { if (answer is Answer.Body) channelNames = it }
    }

    /** Opens the profile again after a refusal. */
    fun retryProfile() {
        val id = profile.value.customerId ?: return
        profile.value = profile.value.copy(error = null, customer = null)
        open(id, profile.value.byCard)
    }

    private companion object {
        fun refusal(error: ApiError): CardRead = when {
            error.code == "card_not_found" -> CardRead.Unknown
            error.code == "card_revoked" -> CardRead.Revoked
            error.code == "invalid_body" -> CardRead.NotACard
            else -> CardRead.Failed(error)
        }
    }
}
