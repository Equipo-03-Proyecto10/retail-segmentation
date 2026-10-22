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
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
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

fun product(id: Int, active: Boolean = true): String =
    """{"product_id":$id,"sku":"MOS-$id","name":"Item $id","category_id":12,""" +
        """"list_price":"129.90","is_active":$active,"image_path":null}"""

/** The products and stock services, as far as a scan reads them. */
class FloorServices : Dispatcher() {
    val products = mutableMapOf(120 to product(120), 311 to product(311, active = false), 245 to product(245))
    val stock = mutableMapOf((3 to 120) to 14, (3 to 311) to 2)

    override fun dispatch(request: RecordedRequest): MockResponse {
        val path = request.path ?: ""
        Regex("^/api/products/v1/products/(\\d+)$").find(path)?.let { match ->
            val body = products[match.groupValues[1].toInt()] ?: return error(404, "product_not_found")
            return ok(200, body)
        }
        Regex("^/api/stock/v1/stock-levels/(\\d+)/(\\d+)$").find(path)?.let { match ->
            val key = match.groupValues[1].toInt() to match.groupValues[2].toInt()
            val quantity = stock[key] ?: return error(404, "stock_level_not_found")
            return ok(
                200,
                """{"store_id":${key.first},"product_id":${key.second},"quantity_on_hand":$quantity,""" +
                    """"updated_at":"2026-10-21T12:00:00Z"}"""
            )
        }
        return error(404, "not_found")
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
    fun anInactiveProductIsShownAsRetired() {
        val model = ScanViewModel(graph)
        model.onDecoded("311")
        val shown = settle(model) as ScanResult.Shown
        assertEquals(false, shown.product.isActive)
        assertEquals(2, shown.quantity)
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
}
