package online.maxthecoder.mosaiq.floor.ui.review

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.time.Clock
import java.time.LocalDate
import java.time.ZoneOffset
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.CaptureIncident
import online.maxthecoder.mosaiq.floor.net.EXPOSURE_PAGE_SIZE
import online.maxthecoder.mosaiq.floor.net.POLL_PAGE_SIZE
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.net.ReviewCaptureIncidentRequest
import online.maxthecoder.mosaiq.floor.net.call
import online.maxthecoder.mosaiq.floor.net.totalPages

const val OPEN = "OPEN"
const val ACKNOWLEDGED = "ACKNOWLEDGED"
const val RESOLVED = "RESOLVED"

/** The states an incident can move to from [status]: forward only, and `OPEN` straight to `RESOLVED` (contracts/engagement). */
fun movesFrom(status: String): List<String> = when (status) {
    OPEN -> listOf(ACKNOWLEDGED, RESOLVED)
    ACKNOWLEDGED -> listOf(RESOLVED)
    else -> emptyList()
}

/** The longest note the contract takes. */
const val NOTE_MAX = 200

/**
 * The incidents of the supervisor's stores, newest first. [busy] holds the incidents whose review is on its
 * way, and [reviewErrors] the answer to a review that was refused, by incident id. [total] is what the service
 * holds, so a list cut at the page limit says so.
 */
data class IncidentsState(
    val loading: Boolean = true,
    val incidents: List<CaptureIncident> = emptyList(),
    val total: Int = 0,
    val error: ApiError? = null,
    val busy: Set<String> = emptySet(),
    val reviewErrors: Map<String, ApiError> = emptyMap()
) {
    fun withStatus(status: String): List<CaptureIncident> = incidents.filter { it.status == status }
}

/** One promotion's showings at one store; [name] is null when the campaign is not among the active ones. */
data class ShownPromotion(val storeId: Int, val campaignId: Int, val name: String?, val times: Int)

/**
 * The promotions shown by store and campaign over the last [days] days, counted on the phone from the exposures
 * engagement lists for the caller's stores. [counted] of [total] records were read when the list is cut at the
 * page limit. No customer is held anywhere in it.
 */
data class PromotionsShownState(
    val loading: Boolean = true,
    val rows: List<ShownPromotion> = emptyList(),
    val since: LocalDate? = null,
    val counted: Int = 0,
    val total: Int = 0,
    val storeNames: Map<Int, String> = emptyMap(),
    val error: ApiError? = null
)

/** One promotion applied at one store, net of reversals; [name] is null when the promotion is not active now. */
data class AppliedPromotion(
    val storeId: Int,
    val promotionId: Int,
    val name: String?,
    val lines: Int,
    val units: Int,
    val discount: String
)

/**
 * The promotions applied in sales by store over the last seven days, as sales-service totals them (F27-16):
 * lines, units and discount, net of returns and cancellations. The service sends only the totals, so no sale and
 * no customer reaches the phone.
 */
data class AppliedPromotionsState(
    val loading: Boolean = true,
    val rows: List<AppliedPromotion> = emptyList(),
    val since: LocalDate? = null,
    val storeNames: Map<Int, String> = emptyMap(),
    val error: ApiError? = null
)

/**
 * The commercial supervisor's review (F27-14): the capture incidents of their stores, which they acknowledge
 * and resolve, the promotions shown, by store, and the promotions applied in sales, by store (F27-16). This
 * phone's syncs come from the outbox. Every read is
 * bounded: a few pages, so the request count does not grow with the data. A review is sent once and never
 * resent by the app: after a lost answer the list is read again, and a second tap on an incident the service
 * already moved is answered 409 and shown as such.
 */
class ReviewViewModel(
    private val graph: AppGraph,
    private val clock: Clock = Clock.systemUTC()
) : ViewModel() {
    val incidents = MutableStateFlow(IncidentsState())
    val promotions = MutableStateFlow(PromotionsShownState())
    val applied = MutableStateFlow(AppliedPromotionsState())
    private var incidentsJob: Job? = null
    private var promotionsJob: Job? = null
    private var appliedJob: Job? = null

    init {
        // Another user's incidents never stay on screen.
        viewModelScope.launch {
            graph.session.userId.collect {
                incidentsJob?.cancel()
                promotionsJob?.cancel()
                appliedJob?.cancel()
                incidents.value = IncidentsState()
                promotions.value = PromotionsShownState()
                applied.value = AppliedPromotionsState()
            }
        }
    }

    fun load() {
        loadIncidents()
        loadPromotions()
        loadApplied()
    }

    fun loadIncidents() {
        val user = graph.session.userId.value ?: return
        incidentsJob?.cancel()
        incidents.value = incidents.value.copy(loading = true, error = null)
        incidentsJob =
            viewModelScope.launch {
                val found = mutableListOf<CaptureIncident>()
                var total = 0
                var page = 1
                while (true) {
                    when (val answer = call { graph.network.engagement.listCaptureIncidents(null, null, page, PAGE) }) {
                        is Answer.Refused -> {
                            if (graph.session.userId.value == user) {
                                incidents.value = incidents.value.copy(loading = false, error = answer.error)
                            }
                            return@launch
                        }
                        is Answer.Body -> {
                            found += answer.value.captureIncidents
                            total = answer.value.pageInfo.total
                            val pages = totalPages(total, answer.value.pageInfo.size)
                            val last = page >= pages || page >= MAX_INCIDENT_PAGES
                            if (last || answer.value.captureIncidents.isEmpty()) break
                            page++
                        }
                    }
                }
                if (graph.session.userId.value != user) return@launch
                // Busy and refused reviews belong to what is on screen, so a reload keeps them.
                incidents.value =
                    incidents.value.copy(
                        loading = false,
                        incidents = found.distinctBy { it.incidentId },
                        total = total,
                        error = null
                    )
            }
    }

    /**
     * Moves [incident] to [status] with an optional [note]. Sent once: the answer replaces the incident in the
     * list. A 409 means the service already holds it at or past [status], so the incident is read again and
     * shown as it now stands, with the refusal beside it.
     */
    fun review(incident: CaptureIncident, status: String, note: String = "") {
        val user = graph.session.userId.value ?: return
        val id = incident.incidentId
        if (id in incidents.value.busy || status !in movesFrom(incident.status)) return
        incidents.value =
            incidents.value.copy(busy = incidents.value.busy + id, reviewErrors = incidents.value.reviewErrors - id)
        val text = note.trim().take(NOTE_MAX).ifEmpty { null }
        viewModelScope.launch {
            val answer =
                call { graph.network.engagement.reviewCaptureIncident(id, ReviewCaptureIncidentRequest(status, text)) }
            if (graph.session.userId.value != user) return@launch
            when (answer) {
                is Answer.Body -> replace(answer.value, id)
                is Answer.Refused -> {
                    incidents.value =
                        incidents.value.copy(
                            busy = incidents.value.busy - id,
                            reviewErrors = incidents.value.reviewErrors + (id to answer.error)
                        )
                    if (answer.error.code == "invalid_transition") reread(id, user)
                }
            }
        }
    }

    private suspend fun reread(id: String, user: String) {
        val answer = call { graph.network.engagement.getCaptureIncident(id) }
        if (answer is Answer.Body && graph.session.userId.value == user) {
            val current = incidents.value
            incidents.value =
                current.copy(
                    incidents = current.incidents.map { if (it.incidentId == id) answer.value else it }
                )
        }
    }

    private fun replace(updated: CaptureIncident, id: String) {
        val current = incidents.value
        incidents.value =
            current.copy(
                incidents = current.incidents.map { if (it.incidentId == id) updated else it },
                busy = current.busy - id,
                reviewErrors = current.reviewErrors - id
            )
    }

    fun loadPromotions() {
        val user = graph.session.userId.value ?: return
        promotionsJob?.cancel()
        val since = LocalDate.now(clock).minusDays(DAYS - 1L)
        promotions.value = promotions.value.copy(loading = true, error = null, since = since)
        promotionsJob =
            viewModelScope.launch {
                val from = since.atStartOfDay(ZoneOffset.UTC).toInstant().toString()
                val counts = linkedMapOf<Pair<Int, Int>, Int>()
                var read = 0
                var total = 0
                var page = 1
                while (true) {
                    val answer =
                        call {
                            graph.network.engagementV2.listEvents(EXPOSURE, from, false, page, EXPOSURE_PAGE_SIZE)
                        }
                    when (answer) {
                        is Answer.Refused -> {
                            if (graph.session.userId.value == user) {
                                promotions.value = promotions.value.copy(loading = false, error = answer.error)
                            }
                            return@launch
                        }
                        is Answer.Body -> {
                            val events = answer.value.events
                            events.forEach { event ->
                                read++
                                event.campaignId?.let { key -> counts.merge(event.storeId to key, 1, Int::plus) }
                            }
                            total = answer.value.pageInfo.total
                            val pages = totalPages(total, answer.value.pageInfo.size)
                            if (page >= pages || page >= MAX_EXPOSURE_PAGES || events.isEmpty()) break
                            page++
                        }
                    }
                }
                val names = campaignNames()
                val stores = storeNames()
                if (graph.session.userId.value != user) return@launch
                promotions.value =
                    PromotionsShownState(
                        loading = false,
                        rows =
                        counts.map { (key, times) -> ShownPromotion(key.first, key.second, names[key.second], times) }
                            .sortedWith(compareBy({ it.storeId }, { -it.times }, { it.campaignId })),
                        since = since,
                        counted = read,
                        total = total,
                        storeNames = stores
                    )
            }
    }

    /**
     * The promotions applied in the caller's stores over the same seven days, one request: sales-service totals
     * them, so the phone counts nothing and the read does not grow with the number of sales.
     */
    fun loadApplied() {
        val user = graph.session.userId.value ?: return
        appliedJob?.cancel()
        val since = LocalDate.now(clock).minusDays(DAYS - 1L)
        applied.value = applied.value.copy(loading = true, error = null, since = since)
        appliedJob =
            viewModelScope.launch {
                val from = since.atStartOfDay(ZoneOffset.UTC).toInstant().toString()
                // The window ends at the start of tomorrow, so today's sales are in it.
                val to = LocalDate.now(clock).plusDays(1).atStartOfDay(ZoneOffset.UTC).toInstant().toString()
                when (val answer = call { graph.network.sales.listSalePromotionTotals(from, to) }) {
                    is Answer.Refused -> {
                        if (graph.session.userId.value == user) {
                            applied.value = applied.value.copy(loading = false, error = answer.error)
                        }
                    }
                    is Answer.Body -> {
                        val names = promotionNames()
                        val stores = storeNames()
                        if (graph.session.userId.value != user) return@launch
                        applied.value =
                            AppliedPromotionsState(
                                loading = false,
                                rows =
                                answer.value.totals.map {
                                    AppliedPromotion(
                                        it.storeId,
                                        it.promotionId,
                                        names[it.promotionId],
                                        it.lines,
                                        it.units,
                                        it.discount
                                    )
                                },
                                since = since,
                                storeNames = stores
                            )
                    }
                }
            }
    }

    /** The active promotions, a few pages at most. Best effort: names only, so a failure leaves numbers. */
    private suspend fun activePromotions(): List<Promotion> {
        val found = mutableListOf<Promotion>()
        var page = 1
        while (page <= MAX_NAME_PAGES) {
            val answer = call { graph.network.campaigns.listActivePromotions(null, true, page, POLL_PAGE_SIZE) }
            if (answer !is Answer.Body) break
            found += answer.value.promotions
            if (page >= totalPages(answer.value.pageInfo.total, answer.value.pageInfo.size)) break
            page++
        }
        return found.sortedBy { it.promotionId }
    }

    /** The active promotions' names by campaign; a campaign no longer active keeps its number. */
    private suspend fun campaignNames(): Map<Int, String> {
        val names = linkedMapOf<Int, String>()
        activePromotions().forEach { names.putIfAbsent(it.campaignId, it.name) }
        return names
    }

    /** The active promotions' names by promotion; a promotion no longer active keeps its number. */
    private suspend fun promotionNames(): Map<Int, String> = activePromotions().associate { it.promotionId to it.name }

    private suspend fun storeNames(): Map<Int, String> =
        (call { graph.network.stores.listStores(true, null, 1, STORE_PAGE) } as? Answer.Body)
            ?.value?.stores?.associate { it.storeId to it.name }.orEmpty()

    companion object {
        /** The last seven UTC days, today included. */
        const val DAYS = 7

        /** The contract's largest page for incidents. */
        const val PAGE = 100

        /** At most 300 incidents are read; a longer list says how many there are. */
        const val MAX_INCIDENT_PAGES = 3

        /** At most 500 exposures are counted; a longer period says how many were read. */
        const val MAX_EXPOSURE_PAGES = 5
        const val MAX_NAME_PAGES = 4
        const val STORE_PAGE = 100
        const val EXPOSURE = "CAMPAIGN_EXPOSURE"
    }
}
