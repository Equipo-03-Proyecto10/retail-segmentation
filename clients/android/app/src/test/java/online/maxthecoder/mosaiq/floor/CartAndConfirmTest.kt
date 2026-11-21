package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.lifecycle.ViewModelStore
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.math.BigDecimal
import java.time.OffsetDateTime
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.CartEventRequest
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.SaleLineRequest
import online.maxthecoder.mosaiq.floor.net.SaleRequest
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.DraftLine
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.session.SaleDraft
import online.maxthecoder.mosaiq.floor.ui.SalesViewModel
import online.maxthecoder.mosaiq.floor.ui.sale.receiptOf
import online.maxthecoder.mosaiq.floor.ui.sale.refusalOf
import online.maxthecoder.mosaiq.floor.ui.sale.saleDiscount
import online.maxthecoder.mosaiq.floor.ui.sale.saleTotal
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * F27-05 (#859): the cart records intent through engagement v2 and never a sale; "Confirm sale" sends
 * one sale with the promotion the seller chose, under one client id generated at that tap, whatever
 * retries or process deaths follow; a refusal is explained and the cart is kept; a recorded sale has
 * a receipt with its id. Built to contracts/sales 1.9.0, campaigns `listPromotions` and stores'
 * commercial rules, all answered here from the contracts' own examples where they exist.
 */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class CartAndConfirmTest {
    private val server = MockWebServer()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private val models = ViewModelStore()
    private val requests = CopyOnWriteArrayList<RecordedRequest>()

    /** What `POST /v1/sales` answers next; the recorded sale by default. */
    private var saleAnswer: () -> MockResponse = { ok(201, contract("sales/examples/record-sale.json")) }

    private val ids = mutableListOf<UUID>()
    private val newId: () -> UUID = { UUID.randomUUID().also { ids += it } }

    private val customer by lazy {
        json.decodeFromString(Customer.serializer(), contract("customers/examples/register-customer.json"))
    }
    private val camera by lazy {
        json.decodeFromString(
            Product.serializer(),
            contract("products/examples/product.json")
        )
    }

    @Before
    fun start() {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                val path = request.requestUrl!!.encodedPath
                return when {
                    path == "/api/sales/v1/channels" -> ok(200, contract("sales/examples/list-channels.json"))
                    path == "/api/stores/v1/stores/3/commercial-rules" ->
                        ok(200, contract("stores/examples/store-commercial-rules.json"))
                    path == "/api/campaigns/v1/promotions" -> ok(200, PROMOTIONS)
                    path == "/api/engagement/v2/engagement-events" -> ok(201, "{}")
                    path == "/api/sales/v1/sales" && request.method == "POST" -> saleAnswer()
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString(), 1)
        graph.tokens.write(Tokens("access", "refresh"))
        graph.session.signedIn(Profile("A", "Manager", "STORE_MANAGER"))
        graph.session.choose(ChosenStore(3, "Coyoacan"))
        runBlocking {
            val checkIn = graph.writes.queueCheckIn(3, null)
            graph.outbox.markDone(checkIn, 201, "{}", null)
        }
    }

    @After
    fun stop() {
        models.clear()
        server.shutdown()
        app.database.close()
    }

    private fun model(): SalesViewModel = SalesViewModel(graph, newId).also {
        models.put("sales-${UUID.randomUUID()}", it)
        idle()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private fun settle(done: () -> Boolean) {
        repeat(400) {
            idle()
            if (done()) return
            Thread.sleep(10)
        }
        error("Operation never finished")
    }

    private fun rows(operation: Operation): List<OutboxRow> =
        runBlocking { graph.outbox.pendingFor("A") }.filter { it.operation == operation }

    private fun drain() = runBlocking {
        OutboxWorker.drain(graph.outbox, graph.session, graph.tokens, graph.network.client, graph.network.gateway)
    }

    /** A cart ready to confirm: channels and promotions loaded, a customer chosen, two cameras on promotion. */
    private fun readyCart(model: SalesViewModel) {
        model.loadChannels()
        settle { model.registration.value.channels.isNotEmpty() }
        model.loadPromotions()
        settle { model.sale.value.discounts.loaded }
        model.selectCustomer(customer)
        model.addProduct(camera)
        model.setQuantity(camera.productId, 2)
        model.choosePromotion(camera.productId, WINTER)
        settle { rows(Operation.CART_ADD).size == 2 }
    }

    @Test
    fun cartEditsQueueCartEventsThroughEngagementV2AndNeverASale() {
        val model = model()
        model.addProduct(camera)
        model.addProduct(camera)
        model.setQuantity(camera.productId, 5)
        model.setQuantity(camera.productId, 2)
        model.setQuantity(camera.productId, 0)
        settle { rows(Operation.CART_ADD).size + rows(Operation.CART_REMOVE).size == 5 }

        val events = runBlocking { graph.outbox.pendingFor("A") }
            .filter { it.operation == Operation.CART_ADD || it.operation == Operation.CART_REMOVE }
        assertEquals(
            listOf("CART_ADD" to 1, "CART_ADD" to 1, "CART_ADD" to 3, "CART_REMOVE" to 3, "CART_REMOVE" to 2),
            events.map {
                json.decodeFromString(
                    CartEventRequest.serializer(),
                    it.body
                ).let { e -> e.type to e.quantity }
            }
        )
        events.forEach {
            assertEquals("api/engagement/v2/engagement-events", it.path)
            val body = json.decodeFromString(CartEventRequest.serializer(), it.body)
            assertEquals(3, body.storeId)
            assertEquals(camera.productId, body.productId)
            assertEquals(it.clientId, body.clientEventId)
        }
        assertEquals("one client event id per tap", 5, events.map { it.clientId }.toSet().size)
        assertTrue(model.sale.value.lines.isEmpty())

        drain()
        val posts = requests.filter { it.method == "POST" }.map { it.requestUrl!!.encodedPath }
        assertEquals(List(5) { "/api/engagement/v2/engagement-events" }, posts)
        assertTrue("nothing reaches POST /v1/sales before confirmation", rows(Operation.SALE).isEmpty())
    }

    @Test
    fun aCartEventCarriesTheCustomerOnceOneIsChosen() {
        val model = model()
        model.selectCustomer(customer)
        model.addProduct(camera)
        settle { rows(Operation.CART_ADD).size == 1 }
        val body = json.decodeFromString(CartEventRequest.serializer(), rows(Operation.CART_ADD).single().body)
        assertEquals(customer.customerId, body.customerId)
    }

    @Test
    fun theSummaryCarriesThePromotionWithinTheStoreCapAndTheSaleNamesIt() {
        val model = model()
        readyCart(model)

        val promotions = requests.first { it.requestUrl!!.encodedPath == "/api/campaigns/v1/promotions" }.requestUrl!!
        assertEquals("3", promotions.queryParameter("store_id"))
        assertEquals("3", promotions.queryParameter("channel_id"))
        assertEquals("true", promotions.queryParameter("active"))
        // Another channel's promotion is not offered, so it cannot be chosen.
        model.choosePromotion(camera.productId, ONLINE_ONLY)
        assertEquals(WINTER, model.sale.value.lines.single().promotion?.promotionId)

        // 20% off, capped at the store's 15%: 129.90 x 0.85 = 110.415, half up to 110.42.
        val sale = model.sale.value
        assertEquals("15.00", sale.discounts.maxDiscountPercent)
        assertEquals(BigDecimal("220.84"), saleTotal(sale))
        assertEquals(BigDecimal("38.96"), saleDiscount(sale))

        model.review()
        assertTrue(model.sale.value.reviewing)
        assertTrue("the summary sends nothing", rows(Operation.SALE).isEmpty())
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null }

        val body = json.decodeFromString(SaleRequest.serializer(), rows(Operation.SALE).single().body)
        assertEquals(listOf(SaleLineRequest(camera.productId, 2, "110.42", WINTER)), body.lines)
        assertTrue(rows(Operation.SALE).single().body.contains("\"promotion_id\":$WINTER"))
        assertEquals(false, model.sale.value.reviewing)
    }

    @Test
    fun aLineWithoutAPromotionSendsTheListPriceAndNoPromotionField() {
        val model = model()
        readyCart(model)
        model.choosePromotion(camera.productId, null)
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null }
        val row = rows(Operation.SALE).single()
        assertEquals("129.90", json.decodeFromString(SaleRequest.serializer(), row.body).lines.single().unitPrice)
        assertTrue(!row.body.contains("promotion_id"))
    }

    @Test
    fun theClientIdIsGeneratedOnceAtTheTapAcrossRetriesAndProcessDeath() {
        val model = model()
        readyCart(model)
        model.confirm(NOW)
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null }
        model.confirm(NOW)
        assertEquals("one id, at the tap", 1, ids.size)
        val row = rows(Operation.SALE).single()
        assertEquals(ids.single().toString(), row.clientId)

        // A killed process: a new view model over the same stored draft and queue.
        val restarted = model()
        assertEquals(row.id, restarted.sale.value.queuedRowId)
        assertEquals(customer.customerId, restarted.selectedCustomer.value?.customerId)
        assertEquals(2, restarted.sale.value.lines.single().quantity)
        restarted.confirm(NOW)
        restarted.addProduct(camera)
        idle()
        assertEquals(1, ids.size)
        assertEquals(1, rows(Operation.SALE).size)

        // The first answer is lost; the resend is the same request under the same key.
        var first = true
        saleAnswer = {
            if (first) {
                first = false
                MockResponse().setResponseCode(504)
            } else {
                ok(200, contract("sales/examples/record-sale.json")).setHeader("Idempotent-Replayed", "true")
            }
        }
        drain()
        drain()
        val posts = requests.filter { it.requestUrl!!.encodedPath == "/api/sales/v1/sales" }
        assertEquals(2, posts.size)
        assertEquals(listOf(row.clientId, row.clientId), posts.map { it.getHeader("Idempotency-Key") })
        assertEquals(posts[0].body.clone().readUtf8(), posts[1].body.clone().readUtf8())

        // The receipt shows the one sale's id.
        settle { restarted.queuedSale.value?.state == RowState.DONE }
        val receipt = receiptOf(restarted.queuedSale.value, restarted.sale.value.lines)!!
        assertEquals(4812L, receipt.saleId)
        assertTrue(receipt.replayed)
        assertEquals("203.25", receipt.total)
    }

    @Test
    fun aTapThatNeverReachedTheQueueIsDroppedOnRestartSoTheNextTapIsTheOnlySale() {
        val lost = UUID.randomUUID().toString()
        graph.drafts.save("A", SaleDraft(customer, listOf(DraftLine(camera, 1)), confirmKey = lost))
        val model = model()
        settle { !model.sale.value.confirming }
        assertNull(model.sale.value.queuedRowId)
        assertEquals(1, model.sale.value.lines.size)
        model.loadChannels()
        settle { model.registration.value.channels.isNotEmpty() }
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null }
        val row = rows(Operation.SALE).single()
        assertEquals(ids.single().toString(), row.clientId)
        assertTrue(row.clientId != lost)
    }

    @Test
    fun aTapWhoseRowWasQueuedBeforeTheProcessDiedLeadsBackToThatSale() {
        val key = UUID.randomUUID()
        val queued = runBlocking {
            graph.writes.queueSale(
                customer.customerId,
                3,
                3,
                listOf(SaleLineRequest(camera.productId, 1, "129.90")),
                NOW
            ) { key }
        }
        // The draft was written with the key, but the process died before it noted the row.
        graph.drafts.save("A", SaleDraft(customer, listOf(DraftLine(camera, 1)), confirmKey = key.toString()))
        val model = model()
        settle { model.sale.value.queuedRowId != null }
        assertEquals(queued, model.sale.value.queuedRowId)
        model.loadChannels()
        settle { model.registration.value.channels.isNotEmpty() }
        model.confirm(NOW)
        idle()
        assertTrue(ids.isEmpty())
        assertEquals(1, rows(Operation.SALE).size)
    }

    @Test
    fun aRefusedDiscountIsExplainedNamesTheLineAndKeepsTheCart() {
        saleAnswer = { ok(422, contract("sales/examples/discount-above-store-cap.json")) }
        val model = model()
        readyCart(model)
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null }
        drain()
        settle { model.queuedSale.value?.state == RowState.REFUSED }

        val refusal = refusalOf(model.queuedSale.value, model.sale.value.lines)!!
        assertEquals("discount_above_store_cap", refusal.error.code)
        assertEquals(AppText.get(R.string.refusal_discount_above_store_cap), refusal.reason)
        assertEquals(camera.name, refusal.line)
        assertEquals(2, model.sale.value.lines.single().quantity)

        // Back to the same cart; the fix is a new sale with a new id, and the refused one is kept.
        model.backToCart()
        assertNull(model.sale.value.queuedRowId)
        assertEquals(WINTER, model.sale.value.lines.single().promotion?.promotionId)
        model.choosePromotion(camera.productId, null)
        saleAnswer = { ok(201, contract("sales/examples/record-sale.json")) }
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null }
        assertEquals(2, ids.size)
        assertEquals(1, rows(Operation.SALE).size)
        assertTrue(rows(Operation.SALE).single().clientId != ids.first().toString())
    }

    @Test
    fun noStockIsExplainedInPlainWordsAndTheCartIsKept() {
        saleAnswer = { ok(409, contract("sales/examples/stock-unavailable.json")) }
        val model = model()
        readyCart(model)
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null }
        drain()
        settle { model.queuedSale.value?.state == RowState.REFUSED }
        val refusal = refusalOf(model.queuedSale.value, model.sale.value.lines)!!
        assertEquals(AppText.get(R.string.refusal_stock_unavailable), refusal.reason)
        // The example names lines[1]; this sale has one line, so no line is named rather than a wrong one.
        assertNull(refusal.line)
        assertEquals(1, model.sale.value.lines.size)
    }

    @Test
    fun everyDiscountRefusalOfTheContractHasItsOwnPlainReason() {
        val codes = listOf(
            "discount-without-promotion",
            "promotion-not-applicable",
            "discount-above-promotion",
            "discount-above-store-cap",
            "stock-unavailable"
        )
        val reasons = codes.map { name ->
            val code = name.replace('-', '_')
            assertTrue(contract("sales/openapi.yaml").contains("`$code`"))
            assertTrue(contract("sales/examples/$name.json").contains("\"$code\""))
            assertNotNull(code, online.maxthecoder.mosaiq.floor.outbox.refusalReason(code))
            online.maxthecoder.mosaiq.floor.outbox.refusalReason(code)
        }
        assertEquals(codes.size, reasons.toSet().size)
    }

    @Test
    fun signingOutForgetsTheDraft() {
        val model = model()
        model.selectCustomer(customer)
        model.addProduct(camera)
        assertNotNull(graph.drafts.load("A"))
        graph.session.signedOut()
        idle()
        assertNull(graph.drafts.load("A"))
        assertTrue(model.sale.value.lines.isEmpty())
    }

    private companion object {
        const val WINTER = 51
        const val ONLINE_ONLY = 52
        val NOW: OffsetDateTime = OffsetDateTime.parse("2026-11-18T12:00:00-06:00")

        /** Two active promotions; only the first is for store 3 and the in-store channel (3). */
        val PROMOTIONS = """
            {"promotions":[
              {"promotion_id":51,"campaign_id":12,"name":"Winter 20","discount_percent":"20.00",
               "starts_on":"2026-11-01","ends_on":"2026-11-30","store_ids":[3],"channel_ids":[3],"is_active":true},
              {"promotion_id":52,"campaign_id":12,"name":"Online only","discount_percent":"10.00",
               "starts_on":"2026-11-01","ends_on":"2026-11-30","store_ids":[3],"channel_ids":[1],"is_active":true}
            ],"page_info":{"page":1,"size":100,"total":2}}
        """.trimIndent()
    }
}
