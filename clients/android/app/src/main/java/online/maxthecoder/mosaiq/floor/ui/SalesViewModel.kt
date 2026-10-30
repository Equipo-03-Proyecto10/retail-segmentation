package online.maxthecoder.mosaiq.floor.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.time.OffsetDateTime
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
import online.maxthecoder.mosaiq.floor.net.RegisterCustomerRequest
import online.maxthecoder.mosaiq.floor.net.ReplaceInterestCategoriesRequest
import online.maxthecoder.mosaiq.floor.net.Sale
import online.maxthecoder.mosaiq.floor.net.SaleLineRequest
import online.maxthecoder.mosaiq.floor.net.call as netCall
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.net.totalPages
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
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

data class SaleLine(val product: Product, val quantity: Int = 1)

data class SaleBuilderState(
    val query: String = "",
    val products: List<Product> = emptyList(),
    val lines: List<SaleLine> = emptyList(),
    val loading: Boolean = false,
    val error: ApiError? = null,
    val confirming: Boolean = false,
    val queuedRowId: Long? = null
)

@OptIn(ExperimentalCoroutinesApi::class)
class SalesViewModel(
    private val graph: AppGraph
) : ViewModel() {
    val customers = MutableStateFlow(CustomerSearchState())
    val registration = MutableStateFlow(RegistrationState())
    val selectedCustomer = MutableStateFlow<Customer?>(null)
    val interests = MutableStateFlow(InterestsState())
    val sale = MutableStateFlow(SaleBuilderState())
    val statusMessage = MutableStateFlow<String?>(null)
    val statusError = MutableStateFlow<ApiError?>(null)
    private var interestJob: Job? = null
    private var owner = graph.session.userId.value

    val history = graph.session.userId.flatMapLatest { user ->
        if (user == null) emptyFlow() else graph.outbox.observeSales(user)
    }.stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    init {
        viewModelScope.launch {
            graph.session.userId.collect { user ->
                if (owner != user) {
                    owner = user
                    interestJob?.cancel()
                    customers.value = CustomerSearchState()
                    registration.value = RegistrationState()
                    selectedCustomer.value = null
                    interests.value = InterestsState()
                    sale.value = SaleBuilderState()
                    statusMessage.value = null
                    statusError.value = null
                }
            }
        }
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

    /** Used by both product search and F17-02's scan result. */
    fun addProduct(product: Product) {
        if (sale.value.confirming || sale.value.queuedRowId != null || !product.isActive) return
        val lines = sale.value.lines.toMutableList()
        val index = lines.indexOfFirst { it.product.productId == product.productId }
        if (index < 0) {
            lines += SaleLine(product)
        } else {
            lines[index] = lines[index].copy(quantity = lines[index].quantity + 1)
        }
        sale.value = sale.value.copy(lines = lines, error = null)
    }

    fun setQuantity(productId: Int, quantity: Int) {
        if (sale.value.confirming || sale.value.queuedRowId != null) return
        val lines =
            if (quantity <= 0) {
                sale.value.lines.filterNot { it.product.productId == productId }
            } else {
                sale.value.lines.map { if (it.product.productId == productId) it.copy(quantity = quantity) else it }
            }
        sale.value = sale.value.copy(lines = lines)
    }

    fun confirm(now: OffsetDateTime = OffsetDateTime.now()) {
        if (sale.value.confirming || sale.value.queuedRowId != null) return
        val user = graph.session.userId.value ?: return
        val customer = selectedCustomer.value ?: return
        val store = graph.session.chosenStore() ?: return
        val channel = registration.value.channels.firstOrNull { it.name == PHYSICAL_STORE } ?: return
        val lines = sale.value.lines.map { SaleLineRequest(it.product.productId, it.quantity, it.product.listPrice) }
        if (lines.isEmpty()) return
        sale.value = sale.value.copy(confirming = true, error = null)
        viewModelScope.launch {
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
                val rowId = graph.writes.queueSale(customer.customerId, store.storeId, channel.channelId, lines, now)
                if (graph.session.userId.value == user) {
                    sale.value = sale.value.copy(queuedRowId = rowId, confirming = false)
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (failure: Exception) {
                sale.value = sale.value.copy(
                    confirming = false,
                    error = ApiError(0, "queue_failed", AppText.get(R.string.sale_error_queue_failed), null)
                )
            }
        }
    }

    fun newSale() {
        if (sale.value.confirming) return
        sale.value = SaleBuilderState()
        statusMessage.value = null
        statusError.value = null
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
        val user = graph.session.userId.value ?: throw CancellationException("Signed out")
        val answer = netCall(block)
        if (graph.session.userId.value != user) throw CancellationException("Session changed")
        return answer
    }

    private companion object {
        const val PHYSICAL_STORE = "physical_store"
    }
}
