package online.maxthecoder.mosaiq.floor.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.auth.RecordPart
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Category
import online.maxthecoder.mosaiq.floor.net.Channel
import online.maxthecoder.mosaiq.floor.net.ConsentDecision
import online.maxthecoder.mosaiq.floor.net.ConsentDecisionInput
import online.maxthecoder.mosaiq.floor.net.ConsentState
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.FloorCustomer
import online.maxthecoder.mosaiq.floor.net.PreferenceChange
import online.maxthecoder.mosaiq.floor.net.PreferenceProfile
import online.maxthecoder.mosaiq.floor.net.RecordConsentDecisionsRequest
import online.maxthecoder.mosaiq.floor.net.RegisterCustomerRequest
import online.maxthecoder.mosaiq.floor.net.ReplaceInterestCategoriesRequest
import online.maxthecoder.mosaiq.floor.net.ReplacePreferredChannelsRequest
import online.maxthecoder.mosaiq.floor.net.call

/** The three purposes of `ConsentPurpose`, in the order the contract lists a customer's state. */
val CONSENT_PURPOSES = listOf("PERSONALIZATION", "MARKETING_CONTACT", "PROFILING")

const val GRANTED = "GRANTED"
const val WITHDRAWN = "WITHDRAWN"

/** `PROGRAMME_DEFAULT`: the backfill's decision, never reported as the customer's own (ADR-0066). */
const val PROGRAMME_DEFAULT = "PROGRAMME_DEFAULT"

/**
 * The new-customer form's server side. [consent] holds only what the officer chose: a purpose
 * with no entry has not been asked yet, and nothing starts as granted. [customer] is set once
 * customers-service registered them, so a consent that failed afterwards is sent again on its
 * own and the customer is never registered twice.
 */
data class EnrolState(
    val channels: List<Channel> = emptyList(),
    val selectedChannelId: Int? = null,
    val loadingChannels: Boolean = false,
    val consent: Map<String, String> = emptyMap(),
    val sending: Boolean = false,
    val customer: Customer? = null,
    val error: ApiError? = null,
    val timeoutAdvice: Boolean = false
) {
    val consentAnswered: Boolean get() = CONSENT_PURPOSES.all { it in consent }
}

/** One part's write: in flight, refused, or just saved. */
data class PartWrite(
    val saving: Boolean = false,
    val saved: Boolean = false,
    val error: ApiError? = null
)

/**
 * A customer's record as a loyalty officer sees it. The drafts start as what the services hold;
 * a save sends the contract's whole set, or the consent decisions the officer changed.
 */
data class RecordState(
    val customerId: String? = null,
    val customer: FloorCustomer? = null,
    val consent: ConsentState? = null,
    val profile: PreferenceProfile? = null,
    val channels: List<Channel> = emptyList(),
    val categories: List<Category> = emptyList(),
    val loading: Boolean = false,
    val error: ApiError? = null,
    val consentDraft: Map<String, String> = emptyMap(),
    val channelsDraft: Set<Int> = emptySet(),
    val interestsDraft: Set<Int> = emptySet(),
    val writes: Map<RecordPart, PartWrite> = emptyMap()
) {
    val ready: Boolean get() = consent != null && profile != null && !loading

    fun write(part: RecordPart): PartWrite = writes[part] ?: PartWrite()

    /** The decisions a consent save sends: a changed answer, or one that confirms a programme default. */
    val consentChanges: List<ConsentDecisionInput>
        get() =
            CONSENT_PURPOSES.mapNotNull { purpose ->
                val chosen = consentDraft[purpose] ?: return@mapNotNull null
                val held = consent?.purposes?.firstOrNull { it.purpose == purpose }
                val same = held != null && held.state == chosen && held.basis != PROGRAMME_DEFAULT
                if (same) null else ConsentDecisionInput(purpose, chosen)
            }

    val channelsChanged: Boolean get() = profile != null && channelsDraft != profile.preferredChannelIds.toSet()

    val interestsChanged: Boolean get() = profile != null && interestsDraft != profile.interestCategoryIds.toSet()
}

/** The consent log and the preference history, newest first; read only when the officer asks. */
data class HistoryState(
    val open: Boolean = false,
    val loading: Boolean = false,
    val consent: List<ConsentDecision> = emptyList(),
    val changes: List<PreferenceChange> = emptyList(),
    val error: ApiError? = null
)

/**
 * The loyalty officer's work on a customer (F27-09): registering them with a consent asked per
 * purpose, and their record of consent, benefits and preferences with its history. Every write
 * goes straight to the service that owns it and is refused there for any other role.
 */
class CustomerRecordViewModel(
    private val graph: AppGraph
) : ViewModel() {
    val enrol = MutableStateFlow(EnrolState())
    val record = MutableStateFlow(RecordState())
    val history = MutableStateFlow(HistoryState())
    private var owner = graph.session.userId.value
    private var recordJob: Job? = null
    private var historyJob: Job? = null

    /** The signed-in user's id, to tell the officer which history rows are their own. */
    val userId: String? get() = graph.session.userId.value

    init {
        viewModelScope.launch {
            graph.session.userId.collect { user ->
                if (user != owner) {
                    owner = user
                    recordJob?.cancel()
                    historyJob?.cancel()
                    enrol.value = EnrolState()
                    record.value = RecordState()
                    history.value = HistoryState()
                }
            }
        }
    }

    private fun stillOwner(user: String?): Boolean = user != null && graph.session.userId.value == user

    // Registration.

    /** A fresh form, for the next customer. */
    fun startEnrolment() {
        val channels = enrol.value.channels
        enrol.value = EnrolState(channels = channels, selectedChannelId = physicalStore(channels))
    }

    fun loadChannels() {
        if (enrol.value.loadingChannels || enrol.value.channels.isNotEmpty()) return
        val user = graph.session.userId.value ?: return
        enrol.value = enrol.value.copy(loadingChannels = true, error = null)
        viewModelScope.launch {
            val answer = call { graph.network.sales.listChannels() }
            if (!stillOwner(user)) return@launch
            enrol.value =
                when (answer) {
                    is Answer.Body -> {
                        val channels = answer.value.channels
                        enrol.value.copy(
                            loadingChannels = false,
                            channels = channels,
                            selectedChannelId = enrol.value.selectedChannelId ?: physicalStore(channels)
                        )
                    }
                    is Answer.Refused -> enrol.value.copy(loadingChannels = false, error = answer.error)
                }
        }
    }

    fun chooseChannel(channelId: Int) {
        if (enrol.value.customer == null) enrol.value = enrol.value.copy(selectedChannelId = channelId)
    }

    /** The officer's answer for one purpose, as the customer gave it. */
    fun answerConsent(purpose: String, decision: String) {
        if (purpose !in CONSENT_PURPOSES || decision !in setOf(GRANTED, WITHDRAWN) || enrol.value.sending) return
        enrol.value = enrol.value.copy(consent = enrol.value.consent + (purpose to decision))
    }

    /**
     * Registers the customer, then records the three consent decisions as answered. Nothing is
     * sent until every purpose has an answer. Registration is a one-shot call, as in the sale
     * flow: when it already succeeded, only the consent is sent again.
     */
    fun submit(name: String, email: String, phone: String, onDone: (String) -> Unit) {
        val state = enrol.value
        if (state.sending || !state.consentAnswered) return
        val user = graph.session.userId.value ?: return
        val registered = state.customer
        if (registered == null && (name.isBlank() || state.selectedChannelId == null)) return
        enrol.value = state.copy(sending = true, error = null, timeoutAdvice = false)
        viewModelScope.launch {
            val customer =
                registered ?: when (val answer = register(name, email, phone, state.selectedChannelId!!)) {
                    is Answer.Body -> answer.value
                    is Answer.Refused -> {
                        if (stillOwner(user)) {
                            enrol.value =
                                enrol.value.copy(
                                    sending = false,
                                    error = answer.error,
                                    timeoutAdvice =
                                    answer.error.status in setOf(502, 504) || answer.error.code == "unreachable"
                                )
                        }
                        return@launch
                    }
                }
            if (!stillOwner(user)) return@launch
            enrol.value = enrol.value.copy(customer = customer)
            val decisions = CONSENT_PURPOSES.map { ConsentDecisionInput(it, enrol.value.consent.getValue(it)) }
            val answer =
                call {
                    graph.network.preferences.recordConsentDecisions(
                        customer.customerId,
                        RecordConsentDecisionsRequest(decisions)
                    )
                }
            if (!stillOwner(user)) return@launch
            when (answer) {
                is Answer.Body -> {
                    enrol.value = EnrolState(channels = enrol.value.channels)
                    onDone(customer.customerId)
                }
                is Answer.Refused -> enrol.value = enrol.value.copy(sending = false, error = answer.error)
            }
        }
    }

    private suspend fun register(name: String, email: String, phone: String, channelId: Int): Answer<Customer> = call {
        graph.network.registration.registerCustomer(
            RegisterCustomerRequest(
                name.trim(),
                email.trim().ifEmpty { null },
                phone.trim().ifEmpty { null },
                channelId
            )
        )
    }

    // The record.

    /** Reads the record of [customerId], unless it is the one already shown. */
    fun open(customerId: String) {
        val current = record.value
        if (current.customerId == customerId && current.error == null && (current.loading || current.ready)) return
        val user = graph.session.userId.value ?: return
        record.value = RecordState(customerId = customerId, loading = true)
        history.value = HistoryState()
        historyJob?.cancel()
        recordJob?.cancel()
        recordJob =
            viewModelScope.launch {
                val customer = async { call { graph.network.customers.getFloorCustomer(customerId) } }
                val consent = async { call { graph.network.preferences.getConsentState(customerId) } }
                val profile = async { call { graph.network.preferences.getProfile(customerId) } }
                val channels = async { call { graph.network.sales.listChannels() } }
                val categories = async { allCategories() }
                val answers =
                    listOf(customer.await(), consent.await(), profile.await(), channels.await(), categories.await())
                if (!stillOwner(user)) return@launch
                val refused = answers.filterIsInstance<Answer.Refused>().firstOrNull()
                record.value =
                    if (refused != null) {
                        RecordState(customerId = customerId, error = refused.error)
                    } else {
                        val held = (profile.await() as Answer.Body).value
                        RecordState(
                            customerId = customerId,
                            customer = (customer.await() as Answer.Body).value,
                            consent = (consent.await() as Answer.Body).value,
                            profile = held,
                            channels = (channels.await() as Answer.Body).value.channels,
                            categories = (categories.await() as Answer.Body).value,
                            consentDraft = answeredState((consent.await() as Answer.Body).value),
                            channelsDraft = held.preferredChannelIds.toSet(),
                            interestsDraft = held.interestCategoryIds.toSet()
                        )
                    }
            }
    }

    fun retry() {
        val id = record.value.customerId ?: return
        record.value = record.value.copy(error = null, loading = false, consent = null)
        open(id)
    }

    fun chooseConsent(purpose: String, decision: String) {
        if (purpose !in CONSENT_PURPOSES || decision !in setOf(GRANTED, WITHDRAWN)) return
        edit(RecordPart.CONSENT) { it.copy(consentDraft = it.consentDraft + (purpose to decision)) }
    }

    fun toggleChannel(channelId: Int) = edit(RecordPart.PREFERRED_CHANNELS) {
        it.copy(channelsDraft = it.channelsDraft.toggle(channelId))
    }

    fun toggleInterest(categoryId: Int) = edit(RecordPart.INTERESTS) {
        it.copy(interestsDraft = it.interestsDraft.toggle(categoryId))
    }

    private fun edit(part: RecordPart, change: (RecordState) -> RecordState) {
        val state = record.value
        if (!state.ready || state.write(part).saving) return
        record.value = change(state).withWrite(part, PartWrite())
    }

    /** Appends the changed decisions to the consent log; the answer is the state of all three purposes. */
    fun saveConsent() {
        val decisions = record.value.consentChanges
        if (decisions.isEmpty()) return
        save(
            RecordPart.CONSENT,
            { id -> graph.network.preferences.recordConsentDecisions(id, RecordConsentDecisionsRequest(decisions)) }
        ) { state, consent -> state.copy(consent = consent, consentDraft = answeredState(consent)) }
    }

    /** PUTs the whole set of preferred channels, an empty one included, never a delta. */
    fun saveChannels() {
        if (!record.value.channelsChanged) return
        val wholeSet = record.value.channelsDraft.sorted()
        save(
            RecordPart.PREFERRED_CHANNELS,
            { id -> graph.network.preferences.replacePreferredChannels(id, ReplacePreferredChannelsRequest(wholeSet)) }
        ) { state, profile -> state.copy(profile = profile, channelsDraft = profile.preferredChannelIds.toSet()) }
    }

    /** PUTs the whole set of declared interests, an empty one included, never a delta. */
    fun saveInterests() {
        if (!record.value.interestsChanged) return
        val wholeSet = record.value.interestsDraft.sorted()
        save(
            RecordPart.INTERESTS,
            { id -> graph.network.preferences.replaceInterests(id, ReplaceInterestCategoriesRequest(wholeSet)) }
        ) { state, profile -> state.copy(profile = profile, interestsDraft = profile.interestCategoryIds.toSet()) }
    }

    private fun <T> save(
        part: RecordPart,
        request: suspend (String) -> retrofit2.Response<T>,
        apply: (RecordState, T) -> RecordState
    ) {
        val state = record.value
        val id = state.customerId ?: return
        if (!state.ready || state.write(part).saving) return
        val user = graph.session.userId.value ?: return
        record.value = state.withWrite(part, PartWrite(saving = true))
        viewModelScope.launch {
            val answer = call { request(id) }
            if (!stillOwner(user) || record.value.customerId != id) return@launch
            record.value =
                when (answer) {
                    is Answer.Body -> apply(record.value, answer.value).withWrite(part, PartWrite(saved = true))
                    is Answer.Refused -> record.value.withWrite(part, PartWrite(error = answer.error))
                }
            // The history shows what was just saved.
            if (answer is Answer.Body) loadHistory()
        }
    }

    // History.

    fun toggleHistory() {
        if (history.value.open) history.value = history.value.copy(open = false) else loadHistory()
    }

    /** The newest page of the consent log and of the preference history. */
    fun loadHistory() {
        val id = record.value.customerId ?: return
        val user = graph.session.userId.value ?: return
        history.value = history.value.copy(open = true, loading = true, error = null)
        historyJob?.cancel()
        historyJob =
            viewModelScope.launch {
                val consent = async { call { graph.network.preferences.listConsentDecisions(id) } }
                val changes = async { call { graph.network.preferences.listPreferenceChanges(id) } }
                val consentAnswer = consent.await()
                val changesAnswer = changes.await()
                if (!stillOwner(user) || record.value.customerId != id) return@launch
                history.value =
                    if (consentAnswer is Answer.Body && changesAnswer is Answer.Body) {
                        HistoryState(
                            open = true,
                            consent = consentAnswer.value.decisions,
                            changes = changesAnswer.value.changes
                        )
                    } else {
                        val error =
                            (consentAnswer as? Answer.Refused)?.error ?: (changesAnswer as Answer.Refused).error
                        HistoryState(open = true, error = error)
                    }
            }
    }

    private suspend fun allCategories(): Answer<List<Category>> {
        val all = mutableListOf<Category>()
        var page = 1
        while (true) {
            when (val answer = call { graph.network.categories.listCategories(page) }) {
                is Answer.Body -> {
                    all += answer.value.categories
                    if (page * answer.value.pageInfo.size >= answer.value.pageInfo.total) return Answer.Body(all)
                    page++
                }
                is Answer.Refused -> return answer
            }
        }
    }

    private companion object {
        const val PHYSICAL_STORE = "physical_store"

        fun physicalStore(channels: List<Channel>): Int? = channels.firstOrNull { it.name == PHYSICAL_STORE }?.channelId

        /** The purposes that have a decision; a purpose never recorded starts with no answer. */
        fun answeredState(consent: ConsentState): Map<String, String> =
            consent.purposes.filter { it.state == GRANTED || it.state == WITHDRAWN }
                .associate { it.purpose to it.state }

        fun Set<Int>.toggle(id: Int): Set<Int> = if (id in this) this - id else this + id

        fun RecordState.withWrite(part: RecordPart, write: PartWrite): RecordState = copy(
            writes = writes + (part to write)
        )
    }
}
