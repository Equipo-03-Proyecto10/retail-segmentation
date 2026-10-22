package online.maxthecoder.mosaiq.floor.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.io.IOException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.scan.Payload
import online.maxthecoder.mosaiq.floor.scan.Scan
import online.maxthecoder.mosaiq.floor.scan.ScanSession
import retrofit2.Response

/** What the scan screen shows for the last code. */
sealed interface ScanResult {
    data object Idle : ScanResult

    /** No checked-in store: the scan needs one first (F17-03). */
    data object NoStore : ScanResult

    data object NotAProduct : ScanResult

    data object ProductNotFound : ScanResult

    data object Looking : ScanResult

    /**
     * The product, and its stock at the store: `quantity` null means the store
     * holds no inventory row for it. `stockError` is a refusal of the stock read
     * itself. The QR_SCAN is queued as `rowId`.
     */
    data class Shown(
        val product: Product,
        val storeName: String,
        val quantity: Int?,
        val stockError: ApiError?,
        val rowId: Long
    ) : ScanResult

    data class Failed(val error: ApiError) : ScanResult
}

/**
 * Scans a product (F17-02): each new code is looked up at products-service and
 * stock-service, and a found product is counted once as a QR_SCAN through the
 * floor-write queue, with the client event id fixed when the code was decoded.
 */
class ScanViewModel(
    private val graph: AppGraph,
    private val session: ScanSession = ScanSession()
) : ViewModel() {
    val result = MutableStateFlow<ScanResult>(ScanResult.Idle)
    private var currentUser = graph.session.userId.value
    private var latestScan: String? = null

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
        }
    }

    private fun stillSignedIn(user: String): Boolean {
        if (graph.session.userId.value == user) return true
        synchronizeSession()
        return false
    }

    private fun show(scan: Scan, user: String, answer: ScanResult) {
        if (stillSignedIn(user) && latestScan == scan.clientEventId) result.value = answer
    }

    /** A frame's decoded code. Repeats of the same code are ignored. */
    fun onDecoded(raw: String) {
        synchronizeSession()
        session.onDecoded(raw)?.let(::handle)
    }

    /** A product id typed by hand counts as a fresh scan, even when repeated. */
    fun onManualEntry(raw: String) {
        session.scanAgain()
        onDecoded(raw)
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
        val payload = scan.payload
        if (payload !is Payload.ProductId) {
            result.value = ScanResult.NotAProduct
            return
        }
        result.value = ScanResult.Looking
        viewModelScope.launch {
            val product =
                when (val answer = call { graph.network.products.getProduct(payload.value) }) {
                    is Answer.Body -> answer.value
                    is Answer.Refused ->
                        return@launch run {
                            show(
                                scan,
                                user,
                                if (answer.error.status == NOT_FOUND) {
                                    ScanResult.ProductNotFound
                                } else {
                                    ScanResult.Failed(answer.error)
                                }
                            )
                        }
                }
            if (!stillSignedIn(user)) return@launch
            var stockError: ApiError? = null
            val quantity =
                when (val answer = call { graph.network.stock.getStockLevel(store.storeId, product.productId) }) {
                    is Answer.Body -> answer.value.quantityOnHand
                    is Answer.Refused -> {
                        if (answer.error.code != STOCK_NOT_FOUND) stockError = answer.error
                        null
                    }
                }
            if (!stillSignedIn(user)) return@launch
            val rowId = graph.writes.queueQrScan(scan, product.productId, store.storeId)
            show(scan, user, ScanResult.Shown(product, store.name, quantity, stockError, rowId))
        }
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

    private companion object {
        const val NOT_FOUND = 404
        const val STOCK_NOT_FOUND = "stock_level_not_found"
    }
}
