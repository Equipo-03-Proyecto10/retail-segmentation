package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.ListenableWorker
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.PromotionsState
import online.maxthecoder.mosaiq.floor.ui.PromotionsViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

private fun example(path: String): String = File("../../../contracts", path).readText()

/** One promotion as campaigns-service sends it (contracts/campaigns `Promotion`). */
private fun promotion(
    id: Int,
    campaign: Int,
    stores: String = "[3]",
    channels: String = "[3]",
    active: Boolean = true
) = """{"promotion_id":$id,"campaign_id":$campaign,"name":"Offer $id","discount_percent":"15.00",""" +
    """"starts_on":"2026-11-01","ends_on":"2026-11-30","store_ids":$stores,"channel_ids":$channels,""" +
    """"is_active":$active}"""

/** sales, campaigns, experimentation and engagement v2, answering with the contracts' examples. */
class PromotionServices : Dispatcher() {
    var promotions = listOf(promotion(38, 12))

    /** The arm per campaign: "control", "treatment", "none" (404) or "down" (503). */
    val arms = mutableMapOf<Int, String>()
    val promotionRequests = CopyOnWriteArrayList<RecordedRequest>()
    val exposures = CopyOnWriteArrayList<RecordedRequest>()

    /** What engagement recorded, by client_event_id. */
    val recorded = linkedSetOf<String>()

    /** What the next exposure POSTs meet: a lost answer after recording, or a refusal before it. */
    val plan = mutableListOf<String>()

    override fun dispatch(request: RecordedRequest): MockResponse {
        val url = request.requestUrl!!
        return when (url.encodedPath) {
            "/api/sales/v1/channels" -> ok(200, example("sales/examples/list-channels.json"))
            "/api/campaigns/v1/promotions" -> {
                promotionRequests += request
                val total = promotions.size
                ok(
                    200,
                    """{"promotions":[${promotions.joinToString(
                        ","
                    )}],"page_info":{"page":1,"size":100,"total":$total}}"""
                )
            }
            "/api/experimentation/v1/experiment-assignments/current" ->
                when (arms[url.queryParameter("campaign_id")!!.toInt()] ?: "none") {
                    "control" -> ok(200, example("experimentation/examples/assignment-current-control.json"))
                    "treatment" -> ok(200, example("experimentation/examples/assignment-current-treatment.json"))
                    "down" -> error(503, "operation_unavailable")
                    else -> ok(404, example("experimentation/examples/assignment-not-found.json"))
                }
            "/api/engagement/v2/engagement-events" -> exposure(request)
            else -> error(404, "not_found")
        }
    }

    private fun exposure(request: RecordedRequest): MockResponse {
        exposures += request
        val failure = plan.removeFirstOrNull()
        if (failure == "unavailable") return error(503, "database_unavailable")
        val id = json.parseToJsonElement(request.body.clone().readUtf8()).jsonObject
            .getValue("client_event_id").jsonPrimitive.content
        val replay = !recorded.add(id)
        if (failure == "lost") return MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE)
        return ok(if (replay) 200 else 201, example("engagement/examples/v2/campaign-exposure.json"))
    }
}

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class PromotionsViewModelTest {
    private val server = MockWebServer()
    private val services = PromotionServices()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private val customer = "c0a80001-0000-4000-8000-000000000205"

    @Before
    fun start() {
        server.dispatcher = services
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString(), timeoutSeconds = 1)
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
        graph.session.choose(ChosenStore(3, "Coyoacan"))
    }

    @After
    fun stop() {
        server.shutdown()
        app.database.close()
    }

    private fun settle(done: () -> Boolean) {
        repeat(300) {
            shadowOf(Looper.getMainLooper()).idle()
            if (done()) return
            Thread.sleep(10)
        }
        error("still waiting")
    }

    private fun loaded(model: PromotionsViewModel = PromotionsViewModel(graph)): PromotionsViewModel {
        model.load(customer)
        settle { !model.state.value.loading }
        return model
    }

    private fun rows() = runBlocking { graph.outbox.pendingFor("A") }

    private fun shownIds(state: PromotionsState) = state.promotions.map { it.promotionId }

    @Test
    fun onlyActivePromotionsForThisStoreAndChannelAreShownWithTheirValidity() {
        services.promotions =
            listOf(
                promotion(38, 12),
                promotion(39, 12, active = false),
                promotion(40, 12, stores = "[1]"),
                promotion(41, 12, channels = "[1]")
            )
        val state = loaded().state.value

        val asked = services.promotionRequests.single().requestUrl!!
        assertEquals("3", asked.queryParameter("store_id"))
        // physical_store is channel 3 in sales-service's example.
        assertEquals("3", asked.queryParameter("channel_id"))
        assertEquals("true", asked.queryParameter("active"))
        assertEquals(listOf(38), shownIds(state))
        assertEquals("2026-11-01", state.promotions.single().startsOn)
        assertEquals("2026-11-30", state.promotions.single().endsOn)
    }

    @Test
    fun aCustomerInControlIsNotShownTheTreatmentAndCannotBeRecordedAsExposed() {
        services.promotions = listOf(promotion(38, 12), promotion(50, 13))
        services.arms[12] = "control"
        services.arms[13] = "treatment"
        val model = loaded()

        assertEquals(listOf(50), shownIds(model.state.value))
        val control = json.decodeFromString(Promotion.serializer(), promotion(38, 12))
        assertFalse(model.record(control))
        settle { true }
        assertTrue(rows().isEmpty())
    }

    @Test
    fun noArmMeansNoExperimentRuleAndAnUnknownArmHoldsTheOfferBack() {
        services.promotions = listOf(promotion(38, 12), promotion(50, 13))
        services.arms[13] = "down"
        val state = loaded().state.value

        assertEquals(listOf(38), shownIds(state))
        assertEquals("operation_unavailable", state.armError?.code)
        assertNull(state.error)
    }

    @Test
    fun loadingTheListRecordsNoExposure() {
        services.promotions = listOf(promotion(38, 12), promotion(50, 13))
        val model = loaded()
        model.load(customer)
        settle { !model.state.value.loading }

        assertEquals(2, model.state.value.promotions.size)
        assertTrue(rows().isEmpty())
        assertTrue(services.exposures.isEmpty())
    }

    @Test
    fun recordingAShownOfferQueuesExactlyOneExposure() {
        val model = loaded()
        val offer = model.state.value.promotions.single()
        assertTrue(model.record(offer))
        assertFalse("a second tap records nothing", model.record(offer))
        settle { rows().isNotEmpty() }

        val row = rows().single()
        assertEquals(Operation.CAMPAIGN_EXPOSURE, row.operation)
        assertEquals("api/engagement/v2/engagement-events", row.path)
        val body = json.parseToJsonElement(row.body).jsonObject
        assertEquals(row.clientId, body.getValue("client_event_id").jsonPrimitive.content)
        assertEquals("CAMPAIGN_EXPOSURE", body.getValue("type").jsonPrimitive.content)
        assertEquals("3", body.getValue("store_id").jsonPrimitive.content)
        assertEquals("12", body.getValue("campaign_id").jsonPrimitive.content)
        assertEquals(customer, body.getValue("customer_id").jsonPrimitive.content)
        assertEquals(setOf(38), model.state.value.recorded)

        // A refresh for the same customer keeps it recorded, so it cannot be recorded twice.
        loaded(model)
        assertFalse(model.record(model.state.value.promotions.single()))
    }

    @Test
    fun anExposureRetriedThroughTwoFailuresIsRecordedOnce() {
        val model = loaded()
        model.record(model.state.value.promotions.single())
        settle { rows().isNotEmpty() }
        val queued = rows().single()
        services.plan += listOf("lost", "unavailable")

        val results =
            (1..3).map {
                runBlocking {
                    OutboxWorker.drain(
                        graph.outbox,
                        graph.session,
                        graph.tokens,
                        graph.network.client,
                        graph.network.gateway
                    )
                }
            }

        assertEquals(listOf(ListenableWorker.Result.retry(), ListenableWorker.Result.retry()), results.take(2))
        val ids = services.exposures.map {
            json.parseToJsonElement(
                it.body.clone().readUtf8()
            ).jsonObject.getValue("client_event_id").jsonPrimitive.content
        }
        assertEquals(3, ids.size)
        assertEquals(1, ids.toSet().size)
        assertEquals(queued.clientId, ids.first())
        assertEquals(1, services.recorded.size)
        assertEquals(RowState.DONE, runBlocking { graph.outbox.find(queued.id) }?.state)
    }

    @Test
    fun withoutAStoreNothingIsAskedAndTheNoteSaysWhy() {
        val noStore = app.build(server.url("/").toString())
        noStore.tokens.write(Tokens("token-A", "refresh-A"))
        noStore.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
        val model = PromotionsViewModel(noStore)
        model.load(customer)

        assertTrue(model.state.value.note!!.contains("Check in"))
        assertTrue(services.promotionRequests.isEmpty())
    }
}
