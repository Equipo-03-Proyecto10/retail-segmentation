package online.maxthecoder.mosaiq.floor.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.time.Instant
import java.time.OffsetDateTime
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.AppText
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Category
import online.maxthecoder.mosaiq.floor.net.Channel
import online.maxthecoder.mosaiq.floor.net.CheckInRequest
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.net.RegisterCustomerRequest
import online.maxthecoder.mosaiq.floor.net.ReplaceInterestCategoriesRequest
import online.maxthecoder.mosaiq.floor.net.Sale
import online.maxthecoder.mosaiq.floor.net.call as netCall
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.net.totalPages
import online.maxthecoder.mosaiq.floor.outbox.FloorWrites
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.DraftLine
import online.maxthecoder.mosaiq.floor.session.SaleDraft
import online.maxthecoder.mosaiq.floor.ui.sale.applicablePromotions
import online.maxthecoder.mosaiq.floor.ui.sale.saleLines
import retrofit2.Response

data class CustomerSearchState(
    val query: String = "",
    val email: String = "",
    val page: Int = 1,
    val totalPages: Int = 1,
    val customers: List<Customer> = emptyList(),
    val loading: Boolean = false,
    val error: ApiError? = null
)

data class RegistrationState(
    val channels: List<Channel> = emptyList(),
    val selectedChannelId: Int? = null,
    val loading: Boolean = false,
    val customer: Customer? = null,
    val error: ApiError? = null,
    val timeoutAdvice: Boolean = false
)

data class InterestsState(
    val categories: List<Category> = emptyList(),
    val selected: Set<Int> = emptySet(),
    val loading: Boolean = false,
    val saved: Boolean = false,
    val ready: Boolean = false,
    val error: ApiError? = null
)

/** A cart line: the product as listed, how many, and the one promotion it names, if any. */
data class SaleLine(val product: Product, val quantity: Int = 1, val promotion: Promotion? = null)

/**
 * The promotions campaigns lists for the checked-in store and the in-store channel, and the store's
 * `max_discount_percent`. A line may name a promotion only once both are [loaded].
 */
data class DiscountState(
    val storeId: Int? = null,
    val channelId: Int? = null,
    val promotions: List<Promotion> = emptyList(),
    val maxDiscountPercent: String? = null,
    val loaded: Boolean = false,
    val loading: Boolean = false,
    val error: ApiError? = null
)

data class SaleBuilderState(
    val query: String = "",
    val products: List<Product> = emptyList(),
    val lines: List<SaleLine> = emptyList(),
    val loading: Boolean = false,
    val error: ApiError? = null,
    val confirming: Boolean = false,
    val queuedRowId: Long? = null,
    val discounts: DiscountState = DiscountState(),
    /** The summary is open: lines, discount and total, waiting for "Confirm sale". */
    val reviewing: Boolean = false
)

@OptIn(ExperimentalCoroutinesApi::class)
class SalesViewModel(
    private val graph: AppGraph,
    /** The confirming tap's client id; a test passes its own to know it. */
    private val newId: () -> UUID = UUID::randomUUID
) : ViewModel() {
    val customers = MutableStateFlow(CustomerSearchState())
    val registration = MutableStateFlow(RegistrationState())
    val selectedCustomer = MutableStateFlow<Customer?>(null)
    val interests = MutableStateFlow(InterestsState())
    val sale = MutableStateFlow(SaleBuilderState())
    val statusMessage = MutableStateFlow<String?>(null)
    val statusError = MutableStateFlow<ApiError?>(null)
    private var interestJob: Job? = null
    private var discountJob: Job? = null
    private var owner = graph.session.userId.value

    /** The client id of the confirming tap, kept until its row is found or known never to exist. */
    private var confirmKey: String? = null

    /** Cart events go into the queue in the order they were tapped. */
    private val cartEvents = Mutex()

    val history = graph.session.userId.flatMapLatest { user ->
        if (user == null) emptyFlow() else graph.outbox.observeSales(user)
    }.stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    init {
        restoreDraft()
        viewModelScope.launch {
            graph.session.userId.collect { user ->
                if (owner != user) {
                    owner = user
                    interestJob?.cancel()
                    discountJob?.cancel()
                    customers.value = CustomerSearchState()
                    registration.value = RegistrationState()
                    selectedCustomer.value = null
                    interests.value = InterestsState()
                    sale.value = SaleBuilderState()
                    confirmKey = null
                    statusMessage.value = null
                    statusError.value = null
                    // A signed-out phone keeps no customer or cart behind.
                    graph.drafts.clear()
                }
            }
        }
    }

    /**
     * Brings back the sale a killed process left: the customer, the cart, and the queued sale. A
     * client id written at a tap whose row is not in the queue never reached it, so nothing was sent
     * with it and it is dropped; one whose row is there takes the seller to that same sale.
     */
    private fun restoreDraft() {
        val user = owner ?: return
        val draft = graph.drafts.load(user) ?: return
        selectedCustomer.value = draft.customer
        sale.value =
            SaleBuilderState(
                lines = draft.lines.map { SaleLine(it.product, it.quantity, it.promotion) },
                queuedRowId = draft.queuedRowId
            )
        confirmKey = draft.confirmKey
        draft.customer?.customerId?.let(::loadInterests)
        val key = draft.confirmKey
        if (key != null && draft.queuedRowId == null) {
            sale.value = sale.value.copy(confirming = true)
            viewModelScope.launch {
                val row = graph.outbox.findByClientId(key)
                if (graph.session.userId.value != user) return@launch
                if (row == null) confirmKey = null
                sale.value = sale.value.copy(confirming = false, queuedRowId = row?.id)
                saveDraft()
            }
        }
    }

    /** Writes the sale being built for the signed-in user; called after every change to it. */
    private fun saveDraft() {
        val user = graph.session.userId.value ?: return
        if (user != owner) return
        graph.drafts.save(
            user,
            SaleDraft(
                customer = selectedCustomer.value,
                lines = sale.value.lines.map { DraftLine(it.product, it.quantity, it.promotion) },
                confirmKey = confirmKey,
                queuedRowId = sale.value.queuedRowId
            )
        )
    }

    val queuedSale: StateFlow<OutboxRow?> =
        sale
            .flatMapLatest { state -> state.queuedRowId?.let(graph.outbox::observe) ?: emptyFlow() }
            .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    fun searchCustomers(query: String = customers.value.query, email: String = customers.value.email, page: Int = 1) {
        customers.value = customers.value.copy(query = query, email = email, page = page, loading = true, error = null)
        viewModelScope.launch {
            when (
                val answer = call {
                    graph.network.customers.searchCustomers(
                        query = query.trim().ifEmpty { null },
                        email = email.trim().ifEmpty { null },
                        page = page
                    )
                }
            ) {
                is Answer.Body -> {
                    val body = answer.value
                    customers.value =
                        customers.value.copy(
                            loading = false,
                            customers = body.customers,
                            totalPages = totalPages(body.pageInfo.total, body.pageInfo.size)
                        )
                }
                is Answer.Refused -> customers.value = customers.value.copy(loading = false, error = answer.error)
            }
        }
    }

    fun loadChannels() {
        if (registration.value.loading || registration.value.channels.isNotEmpty()) return
        registration.value = registration.value.copy(loading = true, error = null)
        viewModelScope.launch {
            when (val answer = call { graph.network.sales.listChannels() }) {
                is Answer.Body -> {
                    val channels = answer.value.channels
                    registration.value =
                        registration.value.copy(
                            loading = false,
                            channels = channels,
                            selectedChannelId =
                            channels.firstOrNull { it.name == PHYSICAL_STORE }?.channelId
                                ?: registration.value.selectedChannelId
                        )
                }
                is Answer.Refused ->
                    registration.value = registration.value.copy(loading = false, error = answer.error)
            }
        }
    }

    fun chooseRegistrationChannel(channelId: Int) {
        registration.value = registration.value.copy(selectedChannelId = channelId)
    }

    /** Registration is deliberately a one-shot online call; no outbox and no automatic retry. */
    fun register(name: String, email: String, phone: String) {
        if (registration.value.loading) return
        val channelId = registration.value.selectedChannelId ?: return
        registration.value =
            registration.value.copy(loading = true, customer = null, error = null, timeoutAdvice = false)
        viewModelScope.launch {
            when (
                val answer =
                    call {
                        graph.network.registration.registerCustomer(
                            RegisterCustomerRequest(
                                name.trim(),
                                email.trim().ifEmpty { null },
                                phone.trim().ifEmpty { null },
                                channelId
                            )
                        )
                    }
            ) {
                is Answer.Body -> {
                    registration.value = registration.value.copy(loading = false, customer = answer.value)
                    selectCustomer(answer.value)
                }
                is Answer.Refused -> registration.value = registration.value.copy(
                    loading = false,
                    error = answer.error,
                    timeoutAdvice = answer.error.status in setOf(502, 504) || answer.error.code == "unreachable"
                )
            }
        }
    }

    fun selectCustomer(customer: Customer) {
        selectedCustomer.value = customer
        saveDraft()
        loadInterests(customer.customerId)
    }

    fun loadInterests() {
        selectedCustomer.value?.customerId?.let(::loadInterests)
    }

    private fun loadInterests(customerId: String) {
        interestJob?.cancel()
        interests.value = InterestsState(loading = true)
        interestJob = viewModelScope.launch {
            val profile = call { graph.network.preferences.getProfile(customerId) }
            val categories = allCategories()
            if (profile is Answer.Body && categories is Answer.Body) {
                interests.value =
                    InterestsState(
                        categories = categories.value,
                        selected = profile.value.interestCategoryIds.toSet(),
                        ready = true
                    )
            } else {
                val error =
                    (profile as? Answer.Refused)?.error
                        ?: (categories as? Answer.Refused)?.error
                        ?: ApiError.unreachable(null)
                interests.value = InterestsState(error = error)
            }
        }
    }

    fun toggleInterest(categoryId: Int) {
        if (!interests.value.ready || interests.value.loading) return
        val chosen = interests.value.selected.toMutableSet()
        if (!chosen.add(categoryId)) chosen.remove(categoryId)
        interests.value = interests.value.copy(selected = chosen, saved = false, error = null)
    }

    /** PUTs the complete edited set, including an empty set, never a delta. */
    fun saveInterests() {
        if (!interests.value.ready || interests.value.loading) return
        val customerId = selectedCustomer.value?.customerId ?: return
        val wholeSet = interests.value.selected.sorted()
        interests.value = interests.value.copy(loading = true, saved = false, error = null)
        interestJob = viewModelScope.launch {
            when (
                val answer =
                    call {
                        graph.network.preferences.replaceInterests(
                            customerId,
                            ReplaceInterestCategoriesRequest(wholeSet)
                        )
                    }
            ) {
                is Answer.Body ->
                    interests.value =
                        interests.value.copy(
                            loading = false,
                            selected = answer.value.interestCategoryIds.toSet(),
                            saved = true
                        )
                is Answer.Refused -> interests.value = interests.value.copy(loading = false, error = answer.error)
            }
        }
    }

    fun searchProducts(query: String) {
        sale.value = sale.value.copy(query = query, loading = true, error = null)
        viewModelScope.launch {
            when (val answer = call { graph.network.products.listProducts(query.trim()) }) {
                is Answer.Body -> sale.value = sale.value.copy(loading = false, products = answer.value.products)
                is Answer.Refused -> sale.value = sale.value.copy(loading = false, error = answer.error)
            }
        }
    }

    private val editable: Boolean
        get() = !sale.value.confirming && sale.value.queuedRowId == null && !sale.value.reviewing

    /**
     * Used by both product search and F17-02's scan result. The line goes into the cart and a
     * `CART_ADD` into the queue; nothing reaches `POST /v1/sales` before "Confirm sale".
     */
    fun addProduct(product: Product, now: Instant = Instant.now()) {
        // A cart event names its store, so the cart opens only once a store is chosen.
        if (!editable || !product.isActive || graph.session.chosenStore() == null) return
        val lines = sale.value.lines.toMutableList()
        val index = lines.indexOfFirst { it.product.productId == product.productId }
        if (index < 0) {
            lines += SaleLine(product)
        } else {
            if (lines[index].quantity >= FloorWrites.MAX_CART_QUANTITY) return
            lines[index] = lines[index].copy(quantity = lines[index].quantity + 1)
        }
        sale.value = sale.value.copy(lines = lines, error = null)
        saveDraft()
        queueCartEvent(Operation.CART_ADD, product.productId, 1, now)
    }

    /**
     * Sets a line's quantity; zero removes the line. A raise queues a `CART_ADD` and a cut or a
     * removal a `CART_REMOVE`, each with the units it moved.
     */
    fun setQuantity(productId: Int, quantity: Int, now: Instant = Instant.now()) {
        if (!editable) return
        val current = sale.value.lines.firstOrNull { it.product.productId == productId } ?: return
        val wanted = quantity.coerceIn(0, FloorWrites.MAX_CART_QUANTITY)
        val lines =
            if (wanted == 0) {
                sale.value.lines.filterNot { it.product.productId == productId }
            } else {
                sale.value.lines.map { if (it.product.productId == productId) it.copy(quantity = wanted) else it }
            }
        sale.value = sale.value.copy(lines = lines)
        saveDraft()
        val moved = wanted - current.quantity
        when {
            moved > 0 -> queueCartEvent(Operation.CART_ADD, productId, moved, now)
            moved < 0 -> queueCartEvent(Operation.CART_REMOVE, productId, -moved, now)
        }
    }

    private fun queueCartEvent(operation: Operation, productId: Int, quantity: Int, now: Instant) {
        val user = graph.session.userId.value ?: return
        val store = graph.session.chosenStore() ?: return
        val customerId = selectedCustomer.value?.customerId
        viewModelScope.launch {
            cartEvents.withLock {
                if (graph.session.userId.value != user) return@withLock
                try {
                    graph.writes.queueCartEvent(operation, store.storeId, productId, quantity, customerId, now)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (failure: Exception) {
                    // A cart event is intent only: losing one never changes the cart or the sale.
                }
            }
        }
    }

    /** Names the promotion a line relies on, or none; only one of the listed, applicable ones. */
    fun choosePromotion(productId: Int, promotionId: Int?) {
        if (!editable) return
        val discounts = sale.value.discounts
        val promotion =
            if (promotionId == null) {
                null
            } else {
                if (!discounts.loaded) return
                applicablePromotions(discounts).firstOrNull { it.promotionId == promotionId } ?: return
            }
        sale.value =
            sale.value.copy(
                lines = sale.value.lines.map {
                    if (it.product.productId == productId) it.copy(promotion = promotion) else it
                }
            )
        saveDraft()
    }

    /**
     * Reads the active promotions for the chosen store and the in-store channel, every page, and the
     * store's discount cap. A line keeps its promotion only while it is still listed.
     */
    fun loadPromotions(force: Boolean = false) {
        val store = graph.session.chosenStore() ?: return
        val channel = registration.value.channels.firstOrNull { it.name == PHYSICAL_STORE } ?: return
        val current = sale.value.discounts
        val same = current.storeId == store.storeId && current.channelId == channel.channelId
        if (same && (current.loading || (current.loaded && !force))) return
        sale.value =
            sale.value.copy(
                discounts = DiscountState(store.storeId, channel.channelId, loading = true)
            )
        discountJob?.cancel()
        discountJob = viewModelScope.launch {
            val rules = call { graph.network.stores.getCommercialRules(store.storeId) }
            val promotions = allPromotions(store.storeId, channel.channelId)
            if (rules is Answer.Body && promotions is Answer.Body) {
                val discounts =
                    DiscountState(
                        store.storeId,
                        channel.channelId,
                        promotions.value,
                        rules.value.maxDiscountPercent,
                        loaded = true
                    )
                val listed = applicablePromotions(discounts).associateBy { it.promotionId }
                sale.value =
                    sale.value.copy(
                        discounts = discounts,
                        lines = if (sale.value.queuedRowId != null) {
                            sale.value.lines
                        } else {
                            sale.value.lines.map { line ->
                                line.copy(promotion = line.promotion?.let { listed[it.promotionId] })
                            }
                        }
                    )
                saveDraft()
            } else {
                val error =
                    (rules as? Answer.Refused)?.error
                        ?: (promotions as? Answer.Refused)?.error
                        ?: ApiError.unexpected()
                sale.value =
                    sale.value.copy(discounts = DiscountState(store.storeId, channel.channelId, error = error))
            }
        }
    }

    private suspend fun allPromotions(storeId: Int, channelId: Int): Answer<List<Promotion>> {
        val all = mutableListOf<Promotion>()
        var page = 1
        do {
            when (val answer = call { graph.network.campaigns.listPromotions(storeId, channelId, page = page) }) {
                is Answer.Body -> {
                    all += answer.value.promotions
                    val info = answer.value.pageInfo
                    if (answer.value.promotions.isEmpty() || page * info.size >= info.total) break
                    page++
                }
                is Answer.Refused -> return answer
            }
        } while (true)
        return Answer.Body(all.distinctBy { it.promotionId })
    }

    /** True while a line names a promotion whose store cap has not been read for this store. */
    private fun discountsPending(): Boolean {
        val state = sale.value
        val store = graph.session.chosenStore()?.storeId
        return state.lines.any { it.promotion != null } &&
            (!state.discounts.loaded || state.discounts.storeId != store)
    }

    /** "Record sale": opens the summary of the lines, the discount and the total. Nothing is sent. */
    fun review() {
        if (!editable || sale.value.lines.isEmpty()) return
        sale.value = sale.value.copy(reviewing = true, error = null)
    }

    /** Leaves the summary for the cart, unchanged. */
    fun cancelReview() {
        if (sale.value.confirming) return
        sale.value = sale.value.copy(reviewing = false)
    }

    /**
     * "Confirm sale". The client id is generated here, once, at this tap, and written with the draft
     * before the sale is queued under it: a resend, a second tap or a killed process all lead back to
     * this one request, never to a second sale (ADR-0041).
     */
    fun confirm(now: OffsetDateTime = OffsetDateTime.now()) {
        if (sale.value.confirming || sale.value.queuedRowId != null) return
        val user = graph.session.userId.value ?: return
        val customer = selectedCustomer.value ?: return
        val store = graph.session.chosenStore() ?: return
        val channel = registration.value.channels.firstOrNull { it.name == PHYSICAL_STORE } ?: return
        if (sale.value.lines.isEmpty()) return
        if (discountsPending()) {
            sale.value = sale.value.copy(
                error = ApiError(0, "promotions_not_loaded", AppText.get(R.string.sale_error_promotions_pending), null)
            )
            return
        }
        val lines = saleLines(sale.value)
        sale.value = sale.value.copy(confirming = true, error = null)
        viewModelScope.launch {
            var key: String? = null
            try {
                val checkIn = graph.outbox.latestCheckIn(user)
                val checkedStore = checkIn?.body?.let { json.decodeFromString(CheckInRequest.serializer(), it).storeId }
                if (graph.session.userId.value != user) return@launch
                if (checkedStore != store.storeId || checkIn.state == RowState.REFUSED) {
                    sale.value = sale.value.copy(
                        confirming = false,
                        error =
                        ApiError(0, "check_in_required", AppText.get(R.string.sale_error_check_in_required), null)
                    )
                    return@launch
                }
                val id = newId()
                key = id.toString().lowercase()
                confirmKey = key
                saveDraft()
                val rowId =
                    graph.writes.queueSale(customer.customerId, store.storeId, channel.channelId, lines, now) { id }
                if (graph.session.userId.value == user) {
                    sale.value = sale.value.copy(queuedRowId = rowId, confirming = false, reviewing = false)
                    saveDraft()
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (failure: Exception) {
                // Only a key whose row is not in the queue may be dropped: nothing was ever sent with it.
                val queued = key?.let { runCatching { graph.outbox.findByClientId(it) }.getOrNull() }
                if (queued != null) {
                    sale.value = sale.value.copy(queuedRowId = queued.id, confirming = false, reviewing = false)
                } else {
                    confirmKey = null
                    sale.value = sale.value.copy(
                        confirming = false,
                        error = ApiError(0, "queue_failed", AppText.get(R.string.sale_error_queue_failed), null)
                    )
                }
                saveDraft()
            }
        }
    }

    /**
     * After a refusal: back to the same cart, to fix what the refusal names and confirm again. That
     * is a new request with a new client id; the refused one stays in the history.
     */
    fun backToCart() {
        if (queuedSale.value?.state != RowState.REFUSED) return
        confirmKey = null
        sale.value = sale.value.copy(queuedRowId = null, reviewing = false, error = null)
        saveDraft()
    }

    fun newSale() {
        if (sale.value.confirming) return
        confirmKey = null
        sale.value = SaleBuilderState(discounts = sale.value.discounts)
        statusMessage.value = null
        statusError.value = null
        saveDraft()
    }

    fun refreshStatus(row: OutboxRow) {
        val recorded = recordedSale(row) ?: return
        statusMessage.value = null
        statusError.value = null
        viewModelScope.launch {
            when (val answer = call { graph.network.sales.getSale(recorded.saleId) }) {
                is Answer.Body ->
                    statusMessage.value =
                        AppText.get(R.string.sale_status_total, answer.value.saleId, formatMoney(answer.value.total))
                is Answer.Refused -> statusError.value = answer.error
            }
        }
    }

    fun status(row: OutboxRow?): String? {
        if (row?.state != RowState.DONE) return null
        val recorded = recordedSale(row) ?: return null
        return if (row.status == 200 && row.code == "idempotent_replayed") {
            AppText.get(R.string.sale_status_replayed, recorded.saleId)
        } else {
            AppText.get(R.string.sale_status_recorded, recorded.saleId)
        }
    }

    private fun recordedSale(row: OutboxRow): Sale? = row.responseBody?.let {
        runCatching { json.decodeFromString(Sale.serializer(), it) }.getOrNull()
    }

    private suspend fun allCategories(): Answer<List<Category>> {
        val all = mutableListOf<Category>()
        var page = 1
        do {
            when (val answer = call { graph.network.categories.listCategories(page) }) {
                is Answer.Body -> {
                    all += answer.value.categories
                    if (page * answer.value.pageInfo.size >= answer.value.pageInfo.total) break
                    page++
                }
                is Answer.Refused -> return answer
            }
        } while (true)
        return Answer.Body(all.distinctBy { it.categoryId })
    }

    private suspend fun <T> call(block: suspend () -> Response<T>): Answer<T> {
        // ui-string-ok: cancellation reasons for the coroutine machinery, never shown
        val user = graph.session.userId.value ?: throw CancellationException("Signed out")
        val answer = netCall(block)
        // ui-string-ok: cancellation reasons for the coroutine machinery, never shown
        if (graph.session.userId.value != user) throw CancellationException("Session changed")
        return answer
    }

    private companion object {
        const val PHYSICAL_STORE = "physical_store"
    }
}
