package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.lifecycle.ViewModelStore
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.time.OffsetDateTime
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.SalesViewModel
import online.maxthecoder.mosaiq.floor.ui.sale.Correction
import online.maxthecoder.mosaiq.floor.ui.sale.SaleUi
import online.maxthecoder.mosaiq.floor.ui.sale.correctionOf
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
 * F27-13 (#867): a refused sale is explained and can be corrected or discarded, wherever it sits in the
 * history and whatever happened since (another sale, a restart); a corrected sale that is recorded settles
 * the refusals it fixes; discarding reports nothing a second time. Real Room, the real worker and the real
 * view model, answered by MockWebServer from the contracts' own examples.
 */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class RefusedSaleTest {
    private val server = MockWebServer()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private val models = ViewModelStore()
    private val posts = CopyOnWriteArrayList<RecordedRequest>()
    private var saleAnswer: () -> MockResponse = { ok(201, contract("sales/examples/record-sale.json")) }
    private val ids = mutableListOf<UUID>()
    private val newId: () -> UUID = { UUID.randomUUID().also { ids += it } }
    private val jobs = mutableListOf<Job>()

    private val customer by lazy {
        json.decodeFromString(Customer.serializer(), contract("customers/examples/register-customer.json"))
    }
    private val camera by lazy {
        json.decodeFromString(Product.serializer(), contract("products/examples/product.json"))
    }

    @Before
    fun start() {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.requestUrl!!.encodedPath
                if (path == SALES && request.method == "POST") posts += request
                return when {
                    path == "/api/sales/v1/channels" -> ok(200, contract("sales/examples/list-channels.json"))
                    path == "/api/stores/v1/stores/3/commercial-rules" ->
                        ok(200, contract("stores/examples/store-commercial-rules.json"))
                    path == "/api/campaigns/v1/promotions" -> ok(200, PROMOTIONS)
                    path == "/api/engagement/v2/engagement-events" -> ok(201, "{}")
                    path == SALES && request.method == "POST" -> saleAnswer()
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
        jobs.forEach { it.cancel() }
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

    private fun sales(): List<OutboxRow> = runBlocking { graph.outbox.observeSales("A").first() }

    private fun pending(operation: Operation): List<OutboxRow> =
        runBlocking { graph.outbox.pendingFor("A") }.filter { it.operation == operation }

    private fun refusedCount(): Int = runBlocking { graph.outbox.observeRefusedCount("A").first() }

    /** One drain, as the worker runs it, reporting each final refusal as the app does. */
    private fun drain() = runBlocking {
        OutboxWorker.drain(
            graph.outbox,
            graph.session,
            graph.tokens,
            graph.network.client,
            graph.network.gateway,
            refused = graph.alerts::refused
        )
    }

    private fun readyCart(model: SalesViewModel) {
        model.loadChannels()
        settle { model.registration.value.channels.isNotEmpty() }
        model.loadPromotions()
        settle { model.sale.value.discounts.loaded }
        model.selectCustomer(customer)
        model.addProduct(camera)
        model.setQuantity(camera.productId, 2)
        model.choosePromotion(camera.productId, WINTER)
    }

    private fun refuse() {
        saleAnswer = { ok(422, contract("sales/examples/discount-above-store-cap.json")) }
    }

    private fun record() {
        saleAnswer = { ok(201, contract("sales/examples/record-sale.json")) }
    }

    /** Confirms the model's cart and has the server refuse it; returns the refused row. */
    private fun confirmRefused(model: SalesViewModel): OutboxRow {
        refuse()
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null }
        drain()
        settle { model.queuedSale.value?.state == RowState.REFUSED }
        return model.queuedSale.value!!
    }

    private fun ui(model: SalesViewModel) = SaleUi(
        customer = model.selectedCustomer.value,
        sale = model.sale.value,
        history = sales(),
        queued = model.queuedSale.value,
        kept = model.kept.value
    )

    @Test
    fun aRefusedSaleIsCorrectedAfterAnotherSaleWasStartedAndRecordedOnceTheFixIsAccepted() {
        val model = model()
        readyCart(model)
        val first = confirmRefused(model)
        settle { first.clientId in model.kept.value }

        model.newSale()
        assertTrue(model.sale.value.lines.isEmpty())
        assertEquals(Correction.AVAILABLE, correctionOf(first, ui(model)))

        model.correct(first)
        // The cart is back as it was confirmed, with its promotion, and no longer a queued sale.
        assertEquals(2, model.sale.value.lines.single().quantity)
        assertEquals(WINTER, model.sale.value.lines.single().promotion?.promotionId)
        assertEquals(customer.customerId, model.selectedCustomer.value?.customerId)
        assertNull(model.sale.value.queuedRowId)
        assertEquals(listOf(first.clientId), model.sale.value.corrects)
        assertEquals(Correction.OPEN, correctionOf(first, ui(model)))
        // Nothing is sent by correcting: the refused sale stays, still refused, until the fix is accepted.
        assertEquals(1, posts.size)
        assertEquals(RowState.REFUSED, sales().single().state)

        model.choosePromotion(camera.productId, null)
        record()
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null }
        assertEquals("a new request, a new id", 2, ids.size)
        assertTrue(sales().first().clientId != first.clientId)
        drain()
        settle { sales().none { it.state == RowState.REFUSED } && graph.drafts.keptIds("A").isEmpty() }

        // The refused sale is settled: it left the list and the needs-attention count.
        assertEquals(listOf(RowState.DONE), sales().map { it.state })
        assertEquals(0, refusedCount())
        assertTrue(graph.drafts.keptIds("A").isEmpty())
        assertEquals(2, posts.size)
        assertTrue(posts[0].getHeader("Idempotency-Key") != posts[1].getHeader("Idempotency-Key"))
    }

    @Test
    fun correctingNeverReplacesTheSaleInProgress() {
        val model = model()
        readyCart(model)
        val first = confirmRefused(model)
        model.newSale()
        model.addProduct(camera)
        assertEquals(1, model.sale.value.lines.single().quantity)
        assertEquals(Correction.BUSY, correctionOf(first, ui(model)))

        model.correct(first)

        assertEquals("the sale in progress is untouched", 1, model.sale.value.lines.single().quantity)
        assertNull(model.sale.value.lines.single().promotion)
        assertTrue(model.sale.value.corrects.isEmpty())
    }

    @Test
    fun aRefusedSaleSurvivesARestartAndIsStillCorrectable() {
        val model = model()
        readyCart(model)
        val first = confirmRefused(model)
        model.newSale()

        val restarted = model()
        settle { first.clientId in restarted.kept.value }
        assertEquals(Correction.AVAILABLE, correctionOf(first, ui(restarted)))
        restarted.correct(first)
        assertEquals(2, restarted.sale.value.lines.single().quantity)

        // Killed again while correcting: the correction is remembered, so the fix still settles the refusal.
        val again = model()
        assertEquals(listOf(first.clientId), again.sale.value.corrects)
        again.choosePromotion(camera.productId, null)
        again.loadChannels()
        settle { again.registration.value.channels.isNotEmpty() }
        record()
        again.confirm(NOW)
        settle { again.sale.value.queuedRowId != null }
        drain()
        settle { sales().none { it.state == RowState.REFUSED } }
        assertEquals(listOf(RowState.DONE), sales().map { it.state })
    }

    @Test
    fun aFixThatIsRefusedAgainSettlesEveryEarlierRefusalOnceItIsRecorded() {
        val model = model()
        readyCart(model)
        val first = confirmRefused(model)
        model.backToCart()
        assertEquals(listOf(first.clientId), model.sale.value.corrects)
        val second = confirmRefused(model)
        assertTrue(second.clientId != first.clientId)
        model.backToCart()
        assertEquals(listOf(second.clientId, first.clientId), model.sale.value.corrects)

        model.choosePromotion(camera.productId, null)
        record()
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null && model.sale.value.queuedRowId != second.id }
        drain()
        settle { sales().none { it.state == RowState.REFUSED } && graph.drafts.keptIds("A").isEmpty() }

        assertEquals(1, sales().size)
        assertEquals(RowState.DONE, sales().single().state)
        assertTrue(graph.drafts.keptIds("A").isEmpty())
    }

    @Test
    fun discardingARefusedSaleRemovesItAndReportsNothingAgain() {
        val model = model()
        readyCart(model)
        val first = confirmRefused(model)
        // The refusal was reported once when it arrived: one incident report queued for it.
        assertEquals(1, pending(Operation.CAPTURE_INCIDENT).size)
        val incidentRows = pending(Operation.CAPTURE_INCIDENT).map { it.id }
        model.newSale()

        model.discard(first)
        settle { sales().isEmpty() && model.kept.value.isEmpty() }

        assertEquals(0, refusedCount())
        assertNull(graph.drafts.kept("A", first.clientId))
        assertEquals(
            "nothing is reported a second time",
            incidentRows,
            pending(Operation.CAPTURE_INCIDENT).map { it.id }
        )
        assertEquals(1, posts.size)
        // The cart in progress, if any, was not touched by discarding a sale of the history.
        assertEquals(Correction.GONE, correctionOf(first, ui(model)))
    }

    @Test
    fun discardingTheSaleOnScreenClearsItsCartToo() {
        val model = model()
        readyCart(model)
        val first = confirmRefused(model)

        model.discard(first)
        settle { sales().isEmpty() }
        settle { model.sale.value.queuedRowId == null }

        assertTrue(model.sale.value.lines.isEmpty())
        assertEquals(0, refusedCount())
    }

    @Test
    fun aRefusedSaleDismissedOnTheOutboxScreenDoesNotLeaveAFalseSaleSavedBehind() {
        val model = model()
        readyCart(model)
        val first = confirmRefused(model)

        runBlocking { graph.outbox.dismiss(first.id) }
        settle { model.sale.value.queuedRowId == null }

        assertTrue(model.sale.value.lines.isEmpty())
    }

    @Test
    fun onlyARefusedSaleOfThisUserCanBeDiscardedOrCorrected() {
        val model = model()
        readyCart(model)
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null }
        val waiting = sales().single()
        assertEquals(RowState.PENDING, waiting.state)

        model.discard(waiting)
        model.correct(waiting)
        idle()

        assertEquals(listOf(waiting), sales())
        assertEquals(waiting.id, model.sale.value.queuedRowId)
    }

    @Test
    fun signingOutKeepsNoCartOfARefusedSaleOnThePhone() {
        val model = model()
        readyCart(model)
        val first = confirmRefused(model)
        model.newSale()
        assertNotNull(graph.drafts.kept("A", first.clientId))

        graph.session.signedOut()
        idle()

        assertTrue(graph.drafts.keptIds("A").isEmpty())
        assertTrue(model.kept.value.isEmpty())
        // The row stays for its owner's next sign-in, and can then only be discarded.
        assertEquals(RowState.REFUSED, sales().single().state)
    }

    @Test
    fun theSavedMessageIsForTheTapThatSavedTheSaleNeverForARestart() {
        val model = model()
        val events = mutableListOf<Unit>()
        jobs += CoroutineScope(Dispatchers.Unconfined).launch { model.saved.collect { events += it } }
        readyCart(model)
        model.confirm(NOW)
        settle { model.sale.value.queuedRowId != null }
        assertEquals(1, events.size)

        val restarted = model()
        val again = mutableListOf<Unit>()
        jobs += CoroutineScope(Dispatchers.Unconfined).launch { restarted.saved.collect { again += it } }
        settle { restarted.sale.value.queuedRowId != null }
        assertTrue("a restart brings the draft back silently", again.isEmpty())
        assertEquals(model.sale.value.queuedRowId, restarted.sale.value.queuedRowId)
    }

    private companion object {
        const val SALES = "/api/sales/v1/sales"
        const val WINTER = 51
        val NOW: OffsetDateTime = OffsetDateTime.parse("2026-11-18T12:00:00-06:00")

        val PROMOTIONS = """
            {"promotions":[
              {"promotion_id":51,"campaign_id":12,"name":"Winter 20","discount_percent":"20.00",
               "starts_on":"2026-11-01","ends_on":"2026-11-30","store_ids":[3],"channel_ids":[3],"is_active":true}
            ],"page_info":{"page":1,"size":100,"total":1}}
        """.trimIndent()
    }
}
