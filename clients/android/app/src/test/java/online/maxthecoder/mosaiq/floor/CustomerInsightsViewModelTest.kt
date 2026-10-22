package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.CustomerInsightsViewModel
import online.maxthecoder.mosaiq.floor.ui.InsightsState
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

private fun example(path: String): String = File("../../../contracts", path).readText()

/** segments, recommendations, products and categories, answering with the contract examples. */
class InsightsServices : Dispatcher() {
    var assignment = "segments/examples/current-assigned.json"
    var recommendations: String = tenItems()
    val productRequests = CopyOnWriteArrayList<RecordedRequest>()

    override fun dispatch(request: RecordedRequest): MockResponse {
        val path = request.requestUrl!!.encodedPath
        return when (path) {
            "/api/segments/v1/segment-assignments/current" -> ok(200, example(assignment))
            "/api/recommendations/v1/recommendations" -> ok(200, recommendations)
            "/api/products/v1/products" -> {
                productRequests += request
                val ids = request.requestUrl!!.queryParameter("ids")!!.split(",").map { it.toInt() }
                val items = ids.joinToString(",") { product(it) }
                ok(200, """{"products":[$items],"page_info":{"page":1,"size":50,"total":${ids.size}}}""")
            }
            "/api/categories/v1/categories" -> ok(200, example("categories/examples/list-categories.json"))
            else -> error(404, "not_found")
        }
    }

    companion object {
        /** The recorded answer, its items repeated with new ids to make a ten-item list. */
        fun tenItems(): String {
            val recorded = Json.parseToJsonElement(example("recommendations/examples/recommended.json")).jsonObject
            val template = recorded.getValue("items").jsonArray
            val items =
                (0 until 10).map { index ->
                    val item = template[index % template.size].jsonObject
                    JsonObject(item + ("product_id" to JsonPrimitive(300 + index)))
                }
            return JsonObject(recorded + ("items" to JsonArray(items))).toString()
        }
    }
}

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class CustomerInsightsViewModelTest {
    private val server = MockWebServer()
    private val services = InsightsServices()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private val customer = "7b3ad781-c5de-4f40-ae50-1fa825ce2b25"

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

    private fun loaded(model: CustomerInsightsViewModel): InsightsState {
        model.load(customer)
        repeat(300) {
            shadowOf(Looper.getMainLooper()).idle()
            if (!model.state.value.loading) return model.state.value
            Thread.sleep(10)
        }
        error("the panel never loaded")
    }

    private fun rows() = runBlocking { graph.outbox.pendingFor("A") }

    @Test
    fun anAssignedSegmentIsShownAsReturned() {
        val state = loaded(CustomerInsightsViewModel(graph))
        val line = CustomerInsightsViewModel.segmentLine(state.assignment!!)
        assertEquals("Segment LOYAL · R 4 F 5 M 4 · since 2026-10-12T03:00:00Z", line)
    }

    @Test
    fun noOpenAssignmentAndAnUnassignedResultAreStatedNeverFilledIn() {
        services.assignment = "segments/examples/current-no-open-assignment.json"
        val none = loaded(CustomerInsightsViewModel(graph)).assignment!!
        assertNull(none.labelCode)
        assertTrue(CustomerInsightsViewModel.segmentLine(none).startsWith("No open assignment"))

        services.assignment = "segments/examples/current-unassigned-result.json"
        val unassigned = loaded(CustomerInsightsViewModel(graph)).assignment!!
        assertNull(unassigned.labelCode)
        assertTrue(CustomerInsightsViewModel.segmentLine(unassigned).startsWith("Unassigned result (run 42)"))
    }

    @Test
    fun aTenItemListNamesItsProductsInExactlyOneProductsRequest() {
        val state = loaded(CustomerInsightsViewModel(graph))
        assertEquals(10, state.items.size)
        assertEquals(1, services.productRequests.size)
        assertEquals(
            (300 until 310).joinToString(","),
            services.productRequests.single().requestUrl!!.queryParameter("ids")
        )
        assertEquals((1..10).toList(), state.items.map { it.rank })
        assertEquals("Item 300", state.items.first().name)
        assertEquals(8, state.items.first().item.quantityOnHand)
        assertEquals(
            listOf(
                "17 other customers in the same segment bought it",
                "In category 10, a category they like",
                "Yogurt makes up 40% of their purchases"
            ),
            state.items.first().reasons
        )
    }

    @Test
    fun eachWayNothingQualifiesIsSaidInPlainWords() {
        for (status in listOf("none-match", "no-open-assignment", "unassigned-result", "no-usual-store")) {
            services.recommendations = example("recommendations/examples/$status.json")
            services.productRequests.clear()
            val state = loaded(CustomerInsightsViewModel(graph))
            assertTrue(state.items.isEmpty())
            assertEquals(0, services.productRequests.size)
            val said = CustomerInsightsViewModel.whyNone(state.recommendations!!.status)
            assertFalse(status, said.contains("("))
        }
    }

    @Test
    fun aTapQueuesOneRecoClickAndOpensTheProductWithoutAScan() {
        val model = CustomerInsightsViewModel(graph)
        val state = loaded(model)
        val opened = mutableListOf<Int>()
        assertTrue(model.open(state.items[1]) { opened += it })
        repeat(100) {
            shadowOf(Looper.getMainLooper()).idle()
            if (rows().isNotEmpty()) return@repeat
            Thread.sleep(10)
        }

        assertEquals(listOf(301), opened)
        val row = rows().single()
        assertEquals(Operation.RECO_CLICK, row.operation)
        val body = Json.parseToJsonElement(row.body).jsonObject
        assertEquals(row.clientId, body.getValue("client_event_id").toString().trim('"'))
        assertEquals("RECO_CLICK", body.getValue("type").toString().trim('"'))
        assertEquals("3", body.getValue("store_id").toString())
        assertEquals("301", body.getValue("product_id").toString())
        assertEquals(customer, body.getValue("customer_id").toString().trim('"'))
        assertEquals("LOYAL", body.getValue("label_code").toString().trim('"'))
        assertEquals("2", body.getValue("rank").toString())
    }

    @Test
    fun aSecondTapOnTheSameItemIsASecondClickWithANewId() {
        val model = CustomerInsightsViewModel(graph)
        val state = loaded(model)
        model.open(state.items[0]) {}
        model.open(state.items[0]) {}
        repeat(100) {
            shadowOf(Looper.getMainLooper()).idle()
            if (rows().size == 2) return@repeat
            Thread.sleep(10)
        }
        val ids = rows().map { it.clientId }
        assertEquals(2, ids.size)
        assertNotEquals(ids[0], ids[1])
    }

    @Test
    fun withoutAStoreTheClickIsNotCountedAndSaysWhy() {
        val noStore = app.build(server.url("/").toString())
        noStore.tokens.write(Tokens("token-A", "refresh-A"))
        noStore.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
        val model = CustomerInsightsViewModel(noStore)
        val state = loaded(model)
        assertFalse(model.open(state.items[0]) {})
        assertTrue(model.state.value.clickNote!!.contains("Check in"))
    }
}
