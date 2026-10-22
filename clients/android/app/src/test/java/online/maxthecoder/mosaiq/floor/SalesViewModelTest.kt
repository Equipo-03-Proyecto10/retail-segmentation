package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.lifecycle.ViewModelStore
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.io.File
import java.time.OffsetDateTime
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.SaleRequest
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.SalesViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

fun contract(path: String): String = File("../../../contracts", path).readText()

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class SalesViewModelTest {
    private val server = MockWebServer()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private lateinit var model: SalesViewModel
    private val models = ViewModelStore()

    @Before
    fun start() {
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString(), 1)
        graph.tokens.write(Tokens("access", "refresh"))
        graph.session.signedIn(Profile("A", "Manager", "STORE_MANAGER"))
        graph.session.choose(ChosenStore(3, "Coyoacan"))
        model = SalesViewModel(graph)
        models.put("sales", model)
        shadowOf(Looper.getMainLooper()).idle()
    }

    @After
    fun stop() {
        models.clear()
        server.shutdown()
        app.database.close()
    }

    private fun settle(done: () -> Boolean) {
        repeat(400) {
            shadowOf(Looper.getMainLooper()).idle()
            if (done()) return
            Thread.sleep(10)
        }
        error("Operation never finished")
    }

    private fun channels() {
        server.enqueue(ok(200, contract("sales/examples/list-channels.json")))
        model.loadChannels()
        settle { !model.registration.value.loading }
        assertEquals("/api/sales/v1/channels", server.takeRequest().path)
        assertEquals(3, model.registration.value.selectedChannelId)
    }

    private fun selectCustomer() {
        server.enqueue(ok(200, contract("customer-preferences/examples/get-profile.json")))
        server.enqueue(ok(200, contract("categories/examples/list-categories.json")))
        model.selectCustomer(
            json.decodeFromString(Customer.serializer(), contract("customers/examples/register-customer.json"))
        )
        settle { !model.interests.value.loading }
        server.takeRequest()
        server.takeRequest()
    }

    @Test
    fun searchSendsLiteralQueryEmailAndPaginationToCustomers() {
        server.enqueue(ok(200, contract("customers/examples/search-literal-percent.json")))
        model.searchCustomers("100% Club_", "ana@example.com", 2)
        settle { !model.customers.value.loading }
        val request = server.takeRequest()
        assertEquals("/api/customers/v1/customers", request.requestUrl!!.encodedPath)
        assertEquals("100% Club_", request.requestUrl!!.queryParameter("q"))
        assertEquals("ana@example.com", request.requestUrl!!.queryParameter("email"))
        assertEquals("2", request.requestUrl!!.queryParameter("page"))
        assertEquals("20", request.requestUrl!!.queryParameter("size"))
        assertEquals("application/json", request.getHeader("Accept"))
        assertTrue(model.customers.value.customers.isNotEmpty())
    }

    @Test
    fun registrationUsesChannelDirectoryAndRequiredFieldsIncludingNullContact() {
        channels()
        server.enqueue(ok(201, contract("customers/examples/register-customer.json")))
        server.enqueue(ok(200, contract("customer-preferences/examples/get-profile.json")))
        server.enqueue(ok(200, contract("categories/examples/list-categories.json")))
        model.register("Ana Torres", "", "")
        settle { !model.registration.value.loading && !model.interests.value.loading }
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/customers/v1/customers", request.path)
        assertEquals(
            """{"name":"Ana Torres","email":null,"phone":null,"registration_channel_id":3}""",
            request.body.readUtf8()
        )
        assertNotNull(model.selectedCustomer.value)
        assertTrue(runBlocking { graph.outbox.pendingFor("A").isEmpty() })
    }

    @Test
    fun registrationPreservesAllContractRefusalsAndFieldDetails() {
        channels()
        for ((code, status) in listOf("email-taken" to 409, "unknown-channel" to 422, "invalid-body" to 422)) {
            server.enqueue(ok(status, contract("customers/examples/$code.json")))
            model.register("Ana", "ana@example.com", "123")
            settle { !model.registration.value.loading }
            server.takeRequest()
            val error = model.registration.value.error!!
            assertEquals(code.replace('-', '_'), error.code)
            assertTrue(error.details.isNotEmpty())
            assertNotNull(error.correlationId)
        }
    }

    @Test
    fun registrationTimeoutIsNeverRetriedAndAdvisesSearchingFirst() {
        channels()
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
        model.register("Ana", "ana@example.com", "123")
        model.register("Ana", "ana@example.com", "123")
        settle { !model.registration.value.loading }
        assertTrue(model.registration.value.timeoutAdvice)
        assertEquals(2, server.requestCount) // one channel GET and one POST
        assertTrue(runBlocking { graph.outbox.pendingFor("A").isEmpty() })
    }

    @Test
    fun registrationDoesNotAutomaticallyRefreshOrFollowRedirectsOrRetry503() {
        channels()
        for (response in listOf(
            error(401, "invalid_token"),
            MockResponse().setResponseCode(307).setHeader("Location", server.url("/elsewhere")),
            error(503, "database_unavailable").setHeader("Retry-After", "0")
        )) {
            val before = server.requestCount
            server.enqueue(response)
            model.register("Ana", "ana@example.com", "123")
            settle { !model.registration.value.loading }
            assertEquals("after ${response.status}", before + 1, server.requestCount)
        }
    }

    @Test
    fun interestsLoadAllPagesAndPutTheWholeEditedSetIncludingClear() {
        server.enqueue(ok(200, contract("customer-preferences/examples/get-profile.json")))
        server.enqueue(
            ok(200, """{"categories":[{"category_id":11,"name":"Cheese"}],"page_info":{"page":1,"size":1,"total":2}}""")
        )
        server.enqueue(
            ok(200, """{"categories":[{"category_id":12,"name":"Yogurt"}],"page_info":{"page":2,"size":1,"total":2}}""")
        )
        model.selectCustomer(
            json.decodeFromString(Customer.serializer(), contract("customers/examples/register-customer.json"))
        )
        settle { !model.interests.value.loading }
        assertEquals(2, model.interests.value.categories.size)
        assertTrue(server.takeRequest().path!!.contains("preference-profiles/"))
        assertTrue(server.takeRequest().path!!.contains("page=1"))
        assertTrue(server.takeRequest().path!!.contains("page=2"))
        model.toggleInterest(11)
        server.enqueue(ok(200, contract("customer-preferences/examples/replace-interest-categories.json")))
        model.saveInterests()
        settle { !model.interests.value.loading }
        val put = server.takeRequest()
        assertEquals("PUT", put.method)
        assertTrue(put.path!!.endsWith("/interest-categories"))
        assertEquals("""{"category_ids":[2,12]}""", put.body.readUtf8())
        model.interests.value.selected.toList().forEach(model::toggleInterest)
        server.enqueue(ok(200, contract("customer-preferences/examples/clear-interest-categories.json")))
        model.saveInterests()
        settle { !model.interests.value.loading }
        assertEquals("""{"category_ids":[]}""", server.takeRequest().body.readUtf8())
    }

    @Test
    fun unknownCategoryIsShownAndFailedProfileLoadCannotClearInterests() {
        selectCustomer()
        server.enqueue(ok(422, contract("customer-preferences/examples/unknown-category.json")))
        model.saveInterests()
        settle { !model.interests.value.loading }
        assertEquals("unknown_category", model.interests.value.error!!.code)
        server.takeRequest()
        server.enqueue(error(503, "database_unavailable"))
        server.enqueue(ok(200, contract("categories/examples/list-categories.json")))
        model.loadInterests()
        settle { !model.interests.value.loading }
        assertFalse(model.interests.value.ready)
        val before = server.requestCount
        model.saveInterests()
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(before, server.requestCount)
    }

    @Test
    fun searchAndScanProductsUseListedPricesAndDoubleConfirmQueuesOneImmutableSale() = runBlocking {
        channels()
        selectCustomer()
        val checkIn = graph.writes.queueCheckIn(3, null)
        graph.outbox.markDone(checkIn, 201, "{}", null)
        server.enqueue(ok(200, contract("products/examples/products.json")))
        model.searchProducts("camera")
        settle { !model.sale.value.loading }
        assertEquals("camera", server.takeRequest().requestUrl!!.queryParameter("q"))
        val product = model.sale.value.products.first()
        model.addProduct(product)
        model.addProduct(product) // same entry point as F17-02 scan's Add to sale
        model.setQuantity(product.productId, 3)
        val now = OffsetDateTime.parse("2026-10-15T14:32:10-06:00")
        model.confirm(now)
        model.confirm(now)
        settle { model.sale.value.queuedRowId != null }
        model.confirm(now)
        val rows = graph.outbox.pendingFor("A").filter { it.operation == Operation.SALE }
        assertEquals(1, rows.size)
        val body = json.decodeFromString(SaleRequest.serializer(), rows.single().body)
        assertEquals(3, body.storeId)
        assertEquals(3, body.channelId)
        assertEquals(now.toString(), body.occurredAt)
        assertEquals(3, body.lines.single().quantity)
        assertEquals(product.listPrice, body.lines.single().unitPrice)
        model.addProduct(product)
        assertEquals(3, model.sale.value.lines.single().quantity)
    }

    @Test
    fun choosingAStoreWithoutCheckingInCannotQueueASale() {
        channels()
        selectCustomer()
        model.addProduct(json.decodeFromString(Product.serializer(), contract("products/examples/product.json")))
        model.confirm()
        settle { !model.sale.value.confirming }
        assertEquals("check_in_required", model.sale.value.error!!.code)
        assertTrue(runBlocking { graph.outbox.pendingFor("A").isEmpty() })
    }

    @Test
    fun anInflightSearchCannotPopulateTheNextUsersScreen() {
        server.enqueue(
            ok(200, contract("customers/examples/search-literal-percent.json")).setBodyDelay(200, TimeUnit.MILLISECONDS)
        )
        model.searchCustomers("Ana")
        graph.session.signedOut()
        graph.session.signedIn(Profile("B", "Other", "STORE_MANAGER"))
        settle { !model.customers.value.loading }
        assertTrue(model.customers.value.customers.isEmpty())
    }
}
