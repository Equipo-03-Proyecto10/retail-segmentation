package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.scan.CodeKind
import online.maxthecoder.mosaiq.floor.scan.Decoded
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.CatalogSearch
import online.maxthecoder.mosaiq.floor.ui.ScanResult
import online.maxthecoder.mosaiq.floor.ui.ScanViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

fun product(id: Int, status: String = "active", barcode: String? = null): String =
    """{"product_id":$id,"sku":"MOS-$id","name":"Item $id","category_id":12,""" +
        """"list_price":"129.90","is_active":${status == "active"},"commercial_status":"$status",""" +
        """"attributes":[],"photo_url":null,"barcode":${barcode?.let { "\"$it\"" } ?: "null"}}"""

/** Valid GTINs: an EAN-13 on product 120, an EAN-8 on 311, and one no product carries. */
const val EAN_13 = "4006381333931"
const val EAN_8 = "96385074"
const val UNKNOWN_EAN_13 = "7501031311309"

/**
 * The products, stock and sales services, as far as a scan or a catalog search
 * reads them. Channel 3 is the physical store, the only one store 3 enables.
 */
class FloorServices : Dispatcher() {
    val products =
        mutableMapOf(
            120 to product(120, barcode = EAN_13),
            311 to product(311, status = "discontinued", barcode = EAN_8),
            312 to product(312, status = "paused"),
            245 to product(245)
        )
    val stock = mutableMapOf((3 to 120) to 14, (3 to 311) to 2, (3 to 312) to 0)
    val stores = setOf(3)
    var channelsStatus = 200

    /** What `listProducts?barcode=` answers: 422 refuses it; `ignoresBarcode` is a service before 1.4.0. */
    var barcodeStatus = 200
    var ignoresBarcode = false

    override fun dispatch(request: RecordedRequest): MockResponse {
        val path = request.path ?: ""
        Regex("^/api/products/v1/products/(\\d+)/details$").find(path)?.let { match ->
            val body = products[match.groupValues[1].toInt()] ?: return error(404, "product_not_found")
            return ok(200, body)
        }
        if (path.startsWith("/api/products/v1/products?barcode=")) {
            if (barcodeStatus != 200) return error(barcodeStatus, "invalid_query")
            val barcode = request.requestUrl!!.queryParameter("barcode")!!
            val found = products.values.filter { ignoresBarcode || it.contains("\"barcode\":\"$barcode\"") }
            val rows = found.joinToString(",")
            return ok(200, """{"products":[$rows],"page_info":{"page":1,"size":20,"total":${found.size}}}""")
        }
        Regex("^/api/products/v1/products\\?q=([^&]*)").find(path)?.let { match ->
            val query = request.requestUrl!!.queryParameter("q")!!.lowercase()
            val found = products.entries.filter { "item ${it.key}".contains(query) || "mos-${it.key}".contains(query) }
            val rows = found.joinToString(",") { it.value }
            return ok(200, """{"products":[$rows],"page_info":{"page":1,"size":20,"total":${found.size}}}""")
        }
        Regex("^/api/stock/v1/availabilities/(\\d+)/(\\d+)$").find(path)?.let { match ->
            val key = match.groupValues[1].toInt() to match.groupValues[2].toInt()
            val details = products[key.second]
            if (key.first !in stores || details == null) return error(404, "availability_not_found")
            return ok(200, availability(key, details.contains("\"is_active\":true"), stock[key]))
        }
        if (path == "/api/sales/v1/channels") {
            if (channelsStatus != 200) return error(channelsStatus, "sales_unavailable")
            val channels = """{"channel_id":1,"name":"online"},{"channel_id":3,"name":"physical_store"}"""
            return ok(200, """{"channels":[$channels]}""")
        }
        return error(404, "not_found")
    }

    /** The first failing test in stock-service's order: sellable, channel, record, quantity. */
    private fun availability(key: Pair<Int, Int>, sellable: Boolean, quantity: Int?): String {
        val rows =
            listOf(1, 3).joinToString(",") { channel ->
                val reason =
                    when {
                        !sellable -> "NOT_SELLABLE"
                        channel != 3 -> "CHANNEL_NOT_ENABLED"
                        quantity == null -> "NO_STOCK_RECORD"
                        quantity <= 0 -> "OUT_OF_STOCK"
                        else -> "AVAILABLE"
                    }
                val stamp = if (quantity == null) "null" else "\"2026-10-21T12:00:00Z\""
                """{"store_id":${key.first},"product_id":${key.second},"channel_id":$channel,""" +
                    """"is_available":${reason == "AVAILABLE"},"reason":"$reason",""" +
                    """"quantity_on_hand":$quantity,"updated_at":$stamp}"""
            }
        return """{"availabilities":[$rows],"page_info":{"page":1,"size":20,"total":2}}"""
    }
}

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class ScanViewModelTest {
    private val server = MockWebServer()
    private val services = FloorServices()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph

    @Before
    fun start() {
        server.dispatcher = services
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString())
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
        graph.session.choose(ChosenStore(3, "Coyoacan"))
    }

    @After
    fun stop() {
        server.shutdown()
        app.database.close()
    }

    /** Lets the lookups finish: network on OkHttp's threads, results on the main looper. */
    private fun settle(model: ScanViewModel): ScanResult {
        repeat(200) {
            shadowOf(Looper.getMainLooper()).idle()
            if (model.result.value != ScanResult.Looking) return model.result.value
            Thread.sleep(10)
        }
        error("the lookup never finished")
    }

    private fun queued() = runBlocking { graph.outbox.pendingFor("A") }

    @Test
    fun aProductInStockIsShownAndCountedOnce() {
        val model = ScanViewModel(graph)
        model.onDecoded("120")
        val shown = settle(model) as ScanResult.Shown
        repeat(10) { model.onDecoded("120") }
        shadowOf(Looper.getMainLooper()).idle()

        assertEquals("Item 120", shown.product.name)
        assertEquals(14, shown.quantity)
        assertNull(shown.stockError)
        assertEquals("active", shown.status)
        assertEquals(3, shown.inStore?.channelId)
        assertEquals("AVAILABLE", shown.inStore?.reason)
        val rows = queued()
        assertEquals(1, rows.size)
        assertEquals(Operation.QR_SCAN, rows.single().operation)
        assertTrue(rows.single().body.contains("\"store_id\":3,\"product_id\":120"))
    }

    @Test
    fun aProductTheStoreDoesNotHoldIsNotStockedNotAnError() {
        val model = ScanViewModel(graph)
        model.onDecoded("245")
        val shown = settle(model) as ScanResult.Shown
        assertNull(shown.quantity)
        assertNull(shown.stockError)
        assertEquals(1, queued().size)
    }

    @Test
    fun aDiscontinuedProductCarriesItsStatusAndIsNotSellableInStore() {
        val model = ScanViewModel(graph)
        model.onDecoded("311")
        val shown = settle(model) as ScanResult.Shown
        assertEquals(false, shown.product.isActive)
        assertEquals("discontinued", shown.status)
        assertEquals("NOT_SELLABLE", shown.inStore?.reason)
        assertEquals(2, shown.quantity)
    }

    @Test
    fun aScanQueuesOneEngagementEventAndNoSale() {
        val model = ScanViewModel(graph)
        model.onDecoded("120")
        settle(model)
        assertEquals(listOf(Operation.QR_SCAN), queued().map { it.operation })
        val paths = (1..server.requestCount).map { server.takeRequest().path ?: "" }
        assertTrue(paths.none { it.startsWith("/api/sales/v1/sales") })
    }

    @Test
    fun theChannelListIsReadOnceForEveryLookUp() {
        val model = ScanViewModel(graph)
        model.onDecoded("120")
        settle(model)
        model.onDecoded("245")
        settle(model)
        val paths = (1..server.requestCount).map { server.takeRequest().path ?: "" }
        assertEquals(1, paths.count { it == "/api/sales/v1/channels" })
    }

    @Test
    fun aRefusedChannelListIsAStockErrorNotAGuess() {
        services.channelsStatus = 503
        val model = ScanViewModel(graph)
        model.onDecoded("120")
        val shown = settle(model) as ScanResult.Shown
        assertEquals("sales_unavailable", shown.stockError?.code)
        assertNull(shown.inStore)
    }

    @Test
    fun aStoreStockServiceDoesNotKnowIsAStockError() {
        graph.session.choose(ChosenStore(99, "Nowhere"))
        val model = ScanViewModel(graph)
        model.onDecoded("120")
        val shown = settle(model) as ScanResult.Shown
        assertEquals("availability_not_found", shown.stockError?.code)
    }

    private fun searched(model: ScanViewModel): CatalogSearch {
        repeat(200) {
            shadowOf(Looper.getMainLooper()).idle()
            if (model.search.value != CatalogSearch.Searching) return model.search.value
            Thread.sleep(10)
        }
        error("the search never finished")
    }

    @Test
    fun aSearchListsTheMatchesAndOpeningOneQueuesNothing() {
        val model = ScanViewModel(graph)
        model.searchCatalog("  item 31 ")
        val found = searched(model) as CatalogSearch.Found
        assertEquals("item 31", found.query)
        assertEquals(listOf(311, 312), found.products.map { it.productId }.sorted())
        assertEquals(2, found.total)

        model.showProduct(312)
        val shown = settle(model) as ScanResult.Shown
        assertEquals("paused", shown.status)
        assertNull(shown.rowId)
        assertTrue(queued().isEmpty())
    }

    @Test
    fun aBlankSearchSendsNothing() {
        val model = ScanViewModel(graph)
        model.searchCatalog("   ")
        assertEquals(CatalogSearch.Idle, model.search.value)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun aRefusedSearchIsShownAsAFailure() {
        val model = ScanViewModel(graph)
        server.dispatcher =
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest) = error(503, "database_unavailable")
            }
        model.searchCatalog("item")
        assertEquals("database_unavailable", (searched(model) as CatalogSearch.Failed).error.code)
    }

    @Test
    fun anotherUserStartsWithAnEmptySearch() {
        val model = ScanViewModel(graph)
        model.searchCatalog("item")
        searched(model)
        graph.session.signedOut()
        graph.session.signedIn(Profile("B", "Demo B", "STORE_MANAGER"))
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(CatalogSearch.Idle, model.search.value)
    }

    private fun barcode(raw: String) = Decoded(raw, CodeKind.PRODUCT_BARCODE)

    private fun paths() = (1..server.requestCount).map { server.takeRequest().path ?: "" }

    @Test
    fun aScannedBarcodeOpensItsProductAndCountsOneScan() {
        val model = ScanViewModel(graph)
        model.onDecoded(barcode(EAN_13))
        val shown = settle(model) as ScanResult.Shown
        repeat(10) { model.onDecoded(barcode(EAN_13)) }
        shadowOf(Looper.getMainLooper()).idle()

        assertEquals(120, shown.product.productId)
        assertEquals(14, shown.quantity)
        assertEquals("AVAILABLE", shown.inStore?.reason)
        val rows = queued()
        assertEquals(listOf(Operation.QR_SCAN), rows.map { it.operation })
        assertTrue(rows.single().body.contains("\"store_id\":3,\"product_id\":120"))
        val paths = paths()
        assertEquals("/api/products/v1/products?barcode=$EAN_13", paths.first())
        assertTrue(paths.none { it.startsWith("/api/sales/v1/sales") })
    }

    @Test
    fun anEightDigitBarcodeIsLookedUpAsABarcodeNotAProductId() {
        val model = ScanViewModel(graph)
        model.onDecoded(barcode(EAN_8))
        val shown = settle(model) as ScanResult.Shown
        assertEquals(311, shown.product.productId)
        assertEquals("discontinued", shown.status)
        assertEquals("/api/products/v1/products?barcode=$EAN_8", paths().first())
    }

    @Test
    fun aBarcodeNoProductCarriesSaysSoAndQueuesNothing() {
        val model = ScanViewModel(graph)
        model.onDecoded(barcode(UNKNOWN_EAN_13))
        assertEquals(ScanResult.BarcodeNotFound(UNKNOWN_EAN_13), settle(model))
        assertTrue(queued().isEmpty())
    }

    @Test
    fun aBarcodeTheServiceRefusesIsSaidPlainlyAndQueuesNothing() {
        services.barcodeStatus = 422
        val model = ScanViewModel(graph)
        model.onDecoded(barcode(EAN_13))
        assertEquals(ScanResult.InvalidBarcode, settle(model))
        assertTrue(queued().isEmpty())
    }

    @Test
    fun aFailedBarcodeLookUpCanBeRetried() {
        services.barcodeStatus = 503
        val model = ScanViewModel(graph)
        model.onDecoded(barcode(EAN_13))
        assertEquals(503, (settle(model) as ScanResult.Failed).error.status)
        services.barcodeStatus = 200
        model.onManualEntry(barcode(EAN_13))
        assertEquals(120, (settle(model) as ScanResult.Shown).product.productId)
        assertEquals(1, queued().size)
    }

    @Test
    fun aServiceThatIgnoresTheFilterCannotOpenTheWrongProduct() {
        services.ignoresBarcode = true
        services.products.keys.retainAll(setOf(245))
        val model = ScanViewModel(graph)
        model.onDecoded(barcode(EAN_13))
        // One product came back, but its details carry no such barcode.
        assertEquals(ScanResult.BarcodeNotFound(EAN_13), settle(model))
        assertTrue(queued().isEmpty())
    }

    @Test
    fun aBarcodeOutsideTheContractPatternIsNotAProductAndSendsNothing() {
        val model = ScanViewModel(graph)
        model.onDecoded(barcode("1234567890"))
        assertEquals(ScanResult.NotAProduct, model.result.value)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun anUnknownProductIsNotFoundAndQueuesNothing() {
        val model = ScanViewModel(graph)
        model.onDecoded("9999")
        assertEquals(ScanResult.ProductNotFound, settle(model))
        assertTrue(queued().isEmpty())
    }

    @Test
    fun aPayloadThatIsNotAProductIdIsSaidSoAndNothingIsSent() {
        val model = ScanViewModel(graph)
        model.onDecoded("https://example.com/promo")
        assertEquals(ScanResult.NotAProduct, model.result.value)
        assertEquals(0, server.requestCount)
        assertTrue(queued().isEmpty())
    }

    @Test
    fun withoutAStoreTheScanAsksForOne() {
        app.database.close()
        // A fresh graph: signed in, but no store chosen yet.
        val noStore = app.build(server.url("/").toString())
        noStore.tokens.write(Tokens("token-A", "refresh-A"))
        noStore.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
        val model = ScanViewModel(noStore)
        model.onDecoded("120")
        assertEquals(ScanResult.NoStore, model.result.value)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun scanAgainCountsTheSameCodeOnceMore() {
        val model = ScanViewModel(graph)
        model.onDecoded("120")
        settle(model)
        model.scanAgain()
        model.onDecoded("120")
        settle(model)
        val rows = queued()
        assertEquals(2, rows.size)
        assertEquals(2, rows.map { it.clientId }.toSet().size)
    }

    @Test
    fun anInflightScanCannotBeQueuedUnderTheNextUser() {
        val model = ScanViewModel(graph)
        model.onDecoded("120")
        graph.session.signedOut()
        graph.session.signedIn(Profile("B", "Demo B", "STORE_MANAGER"))
        graph.session.choose(ChosenStore(3, "Coyoacan"))
        settle(model)
        assertTrue(runBlocking { graph.outbox.pendingFor("B") }.isEmpty())
    }

    @Test
    fun aNewUserCanScanTheSameCodeAsThePreviousUser() {
        val model = ScanViewModel(graph)
        model.onDecoded("120")
        settle(model)
        graph.session.signedOut()
        graph.session.signedIn(Profile("B", "Demo B", "STORE_MANAGER"))
        graph.session.choose(ChosenStore(3, "Coyoacan"))
        model.onDecoded("120")
        settle(model)
        assertEquals(1, runBlocking { graph.outbox.pendingFor("B") }.size)
    }
}
