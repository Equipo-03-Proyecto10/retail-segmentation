package online.maxthecoder.mosaiq.floor.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.util.UUID
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Availability
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.call
import online.maxthecoder.mosaiq.floor.scan.Decoded
import online.maxthecoder.mosaiq.floor.scan.Payload
import online.maxthecoder.mosaiq.floor.scan.Scan
import online.maxthecoder.mosaiq.floor.scan.ScanSession
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.sale.PHYSICAL_STORE

/** What the scan screen shows for the last code. */
sealed interface ScanResult {
    data object Idle : ScanResult

    /** No checked-in store: the scan needs one first (F17-03). */
    data object NoStore : ScanResult

    data object NotAProduct : ScanResult

    data object ProductNotFound : ScanResult

    /** A product barcode no product carries (F27-04). Nothing is queued. */
    data class BarcodeNotFound(val barcode: String) : ScanResult

    /** products-service refused the barcode as malformed: 422 `invalid_query` (F27-04). */
    data object InvalidBarcode : ScanResult

    data object Looking : ScanResult

    /**
     * The product, and its stock at the store: `quantity` null means the store
     * holds no inventory row for it. `stockError` is a refusal of the stock read
     * itself. A scanned product's QR_SCAN is queued as `rowId`; a product opened
     * from a recommendation or a search queues none here (a RECO_CLICK is its own
     * row). `status` is the commercial status, and `inStore` the availability row
     * of the physical-store channel, the one a floor sale is recorded under (F27-04).
     */
    data class Shown(
        val product: Product,
        val storeName: String,
        val quantity: Int?,
        val stockError: ApiError?,
        val rowId: Long?,
        val status: String? = null,
        val inStore: Availability? = null
    ) : ScanResult

    data class Failed(val error: ApiError) : ScanResult
}

/** The catalog search under the scanner (F27-04): `listProducts?q=`, first page only. */
sealed interface CatalogSearch {
    data object Idle : CatalogSearch

    data object Searching : CatalogSearch

    /** `total` counts every match; `products` is the first page of them. */
    data class Found(val query: String, val products: List<Product>, val total: Int) : CatalogSearch

    data class Failed(val error: ApiError) : CatalogSearch
}

/**
 * Scans a product (F17-02): each new code is looked up at products-service and
 * stock-service, and a found product is counted once as a QR_SCAN through the
 * floor-write queue, with the client event id fixed when the code was decoded.
 * A product barcode is first turned into its product by `listProducts?barcode=`
 * (F27-04); from there it is the same look-up and the same one QR_SCAN (ADR-0041).
 */
class ScanViewModel(
    private val graph: AppGraph,
    private val session: ScanSession = ScanSession()
) : ViewModel() {
    val result = MutableStateFlow<ScanResult>(ScanResult.Idle)
    val search = MutableStateFlow<CatalogSearch>(CatalogSearch.Idle)
    private var currentUser = graph.session.userId.value
    private var latestScan: String? = null
    private var latestSearch: String? = null

    /** sales' id of the physical-store channel, read once per view model. */
    private var inStoreChannelId: Int? = null

    init {
        viewModelScope.launch {
            graph.session.userId.collect { synchronizeSession() }
        }
    }

    private fun synchronizeSession() {
        val user = graph.session.userId.value
        if (user != currentUser) {
            currentUser = user
            scanAgain()
            latestSearch = null
            search.value = CatalogSearch.Idle
        }
    }

    private fun stillSignedIn(user: String): Boolean {
        if (graph.session.userId.value == user) return true
        synchronizeSession()
        return false
    }

    private fun show(token: String, user: String, answer: ScanResult) {
        if (stillSignedIn(user) && latestScan == token) result.value = answer
    }

    /** A frame's decoded product QR code. Repeats of the same code are ignored. */
    fun onDecoded(raw: String) = onDecoded(Decoded(raw))

    /** A frame's decoded code, with the kind the camera read it as. */
    fun onDecoded(decoded: Decoded) {
        synchronizeSession()
        session.onDecoded(decoded)?.let(::handle)
    }

    /** A product id typed by hand counts as a fresh scan, even when repeated. */
    fun onManualEntry(raw: String) = onManualEntry(Decoded(raw))

    /** A code looked up again by hand, as what it was first read as. */
    fun onManualEntry(decoded: Decoded) {
        session.scanAgain()
        onDecoded(decoded)
    }

    /** "Scan again": the same code may count once more. */
    fun scanAgain() {
        latestScan = null
        session.scanAgain()
        result.value = ScanResult.Idle
    }

    private fun handle(scan: Scan) {
        val user = graph.session.userId.value ?: return
        latestScan = scan.clientEventId
        val store = graph.session.chosenStore()
        if (store == null) {
            result.value = ScanResult.NoStore
            return
        }
        val count: suspend (Product) -> Long? = { product ->
            graph.writes.queueQrScan(scan, product.productId, store.storeId)
        }
        when (val payload = scan.payload) {
            is Payload.ProductId -> lookUp(scan.clientEventId, user, store, payload.value, count)
            is Payload.Barcode -> lookUpBarcode(scan.clientEventId, user, store, payload.value, count)
            Payload.NotAProduct -> result.value = ScanResult.NotAProduct
        }
    }

    /**
     * Searches the catalog by name or SKU. A newer search, or another user, wins
     * over an answer still on its way.
     */
    fun searchCatalog(raw: String) {
        synchronizeSession()
        val query = raw.trim().take(MAX_QUERY)
        val user = graph.session.userId.value ?: return
        if (query.isEmpty()) return
        val token = UUID.randomUUID().toString()
        latestSearch = token
        search.value = CatalogSearch.Searching
        viewModelScope.launch {
            val next =
                when (val answer = call { graph.network.products.listProducts(query) }) {
                    is Answer.Body -> CatalogSearch.Found(query, answer.value.products, answer.value.pageInfo.total)
                    is Answer.Refused -> CatalogSearch.Failed(answer.error)
                }
            if (stillSignedIn(user) && latestSearch == token) search.value = next
        }
    }

    /**
     * Opens a product without scanning it, from a recommendation (F17-05) or a
     * search (F27-04): the same detail, product and stock at the checked-in store,
     * but no QR_SCAN, since nothing was scanned.
     */
    fun showProduct(productId: Int) {
        synchronizeSession()
        val user = graph.session.userId.value ?: return
        session.scanAgain()
        val token = "open:" + UUID.randomUUID()
        latestScan = token
        val store = graph.session.chosenStore()
        if (store == null) {
            result.value = ScanResult.NoStore
            return
        }
        lookUp(token, user, store, productId) { null }
    }

    private fun lookUp(
        token: String,
        user: String,
        store: ChosenStore,
        productId: Int,
        count: suspend (Product) -> Long?
    ) {
        result.value = ScanResult.Looking
        viewModelScope.launch { showLookUp(token, user, store, productId, null, count) }
    }

    /**
     * Finds the product that carries `barcode` through `listProducts?barcode=`,
     * then looks it up as a scanned product. Barcodes are unique, so an empty page
     * is a barcode no product carries. The list alone is not trusted: the details
     * must carry the same barcode, so a products-service that ignored the filter
     * cannot open the wrong product.
     */
    private fun lookUpBarcode(
        token: String,
        user: String,
        store: ChosenStore,
        barcode: String,
        count: suspend (Product) -> Long?
    ) {
        result.value = ScanResult.Looking
        viewModelScope.launch {
            val found =
                when (val answer = call { graph.network.products.listProductsByBarcode(barcode) }) {
                    is Answer.Body -> answer.value.products
                    is Answer.Refused ->
                        return@launch show(
                            token,
                            user,
                            if (answer.error.status == UNPROCESSABLE) {
                                ScanResult.InvalidBarcode
                            } else {
                                ScanResult.Failed(answer.error)
                            }
                        )
                }
            if (!stillSignedIn(user)) return@launch
            val product = found.singleOrNull()
            if (product == null) {
                show(token, user, ScanResult.BarcodeNotFound(barcode))
                return@launch
            }
            showLookUp(token, user, store, product.productId, barcode, count)
        }
    }

    /**
     * Reads the product with its commercial status, then its availability at `store`;
     * `count` queues what the look-up counts as. The store holds one quantity, the
     * same on every channel row (ADR-0061); no rows at all means no stock record.
     * A product found by `barcode` must carry that barcode in its details.
     */
    private suspend fun showLookUp(
        token: String,
        user: String,
        store: ChosenStore,
        productId: Int,
        barcode: String?,
        count: suspend (Product) -> Long?
    ) {
        val notFound = if (barcode == null) ScanResult.ProductNotFound else ScanResult.BarcodeNotFound(barcode)
        val details =
            when (val answer = call { graph.network.products.getProductDetails(productId) }) {
                is Answer.Body -> answer.value
                is Answer.Refused ->
                    return show(
                        token,
                        user,
                        if (answer.error.status == NOT_FOUND) notFound else ScanResult.Failed(answer.error)
                    )
            }
        if (!stillSignedIn(user)) return
        if (barcode != null && details.barcode != barcode) return show(token, user, notFound)
        val product = details.product
        var stockError: ApiError? = null
        var rows: List<Availability> = emptyList()
        when (val answer = call { graph.network.stock.getAvailability(store.storeId, product.productId) }) {
            is Answer.Body -> rows = answer.value.availabilities
            is Answer.Refused -> stockError = answer.error
        }
        var channelId: Int? = null
        if (stockError == null) {
            val (id, refusal) = inStoreChannel()
            channelId = id
            stockError = refusal
        }
        if (!stillSignedIn(user)) return
        val rowId = count(product)
        val quantity = rows.firstNotNullOfOrNull { it.quantityOnHand }
        val inStore = rows.firstOrNull { it.channelId == channelId }
        show(
            token,
            user,
            ScanResult.Shown(product, store.name, quantity, stockError, rowId, details.commercialStatus, inStore)
        )
    }

    /** The physical-store channel's id, or the refusal of the channel list. */
    private suspend fun inStoreChannel(): Pair<Int?, ApiError?> {
        inStoreChannelId?.let { return it to null }
        return when (val answer = call { graph.network.sales.listChannels() }) {
            is Answer.Body -> {
                inStoreChannelId = answer.value.channels.firstOrNull { it.name == PHYSICAL_STORE }?.channelId
                inStoreChannelId to null
            }
            is Answer.Refused -> null to answer.error
        }
    }

    private companion object {
        const val NOT_FOUND = 404
        const val UNPROCESSABLE = 422
        const val MAX_QUERY = 150
    }
}
