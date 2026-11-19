package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertHasNoClickAction
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onChildAt
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.ExplainedState
import online.maxthecoder.mosaiq.floor.ui.IdentifyViewModel
import online.maxthecoder.mosaiq.floor.ui.identify.RecommendationsPart
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

private fun shelfContract(path: String): String = File("../../../contracts", path).readText()

private const val QUIET_ROUNDS = 20

private const val WITH_ALTERNATIVES = "recommendations/examples/v2/recommended-with-alternatives.json"

/** recommendations `/v2`, products, categories and stores, answering from the contracts' examples. */
private class ShelfServices : Dispatcher() {
    var answer: String = shelfContract(WITH_ALTERNATIVES)
    val asked = CopyOnWriteArrayList<RecordedRequest>()

    override fun dispatch(request: RecordedRequest): MockResponse {
        val url = request.requestUrl!!
        return when (url.encodedPath) {
            "/api/recommendations/v2/recommendations" -> {
                asked += request
                ok(200, answer)
            }
            "/api/products/v1/products" -> {
                val ids = url.queryParameter("ids")!!.split(",").map { it.toInt() }
                val items = ids.joinToString(",") { product(it) }
                ok(200, """{"products":[$items],"page_info":{"page":1,"size":50,"total":${ids.size}}}""")
            }
            "/api/categories/v1/categories" -> ok(200, shelfContract("categories/examples/list-categories.json"))
            "/api/stores/v1/stores" ->
                ok(
                    200,
                    """{"stores":[{"store_id":3,"name":"Del Valle","city":"CDMX","state":"CDMX","is_active":true}],""" +
                        """"page_info":{"page":1,"size":100,"total":1}}"""
                )
            else -> error(404, "not_found")
        }
    }

    companion object {
        /** The recorded answer's items, repeated with new ids and positions up to [count]. */
        fun many(count: Int): String {
            val recorded = Json.parseToJsonElement(shelfContract(WITH_ALTERNATIVES)).jsonObject
            val template = recorded.getValue("items").jsonArray
            val items =
                (0 until count).map { index ->
                    val item = template[index % template.size].jsonObject
                    JsonObject(
                        item +
                            mapOf(
                                "product_id" to JsonPrimitive(500 + index),
                                "position" to JsonPrimitive(index + 1)
                            )
                    )
                }
            return JsonObject(recorded + ("items" to JsonArray(items))).toString()
        }

        fun empty(status: String): String {
            val recorded = Json.parseToJsonElement(shelfContract(WITH_ALTERNATIVES)).jsonObject
            return JsonObject(
                recorded + mapOf("status" to JsonPrimitive(status), "items" to JsonArray(emptyList()))
            ).toString()
        }
    }
}

/**
 * F27-06 (#860): recommendations judged at the checked-in store, each item explained, an
 * alternative or another store's stock marked, the service's reason when nothing is offered,
 * one `RECO_IMPRESSION` per item really shown and a separate `RECO_CLICK` per tap.
 */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class ExplainedRecommendationsTest {
    @get:Rule val compose = createComposeRule()

    private val server = MockWebServer()
    private val services = ShelfServices()
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
        signIn("SALES_ASSOCIATE")
        graph.session.choose(ChosenStore(5, "Polanco"))
    }

    @After
    fun stop() {
        // What the screen queued is written before the database goes.
        repeat(QUIET_ROUNDS) {
            shadowOf(Looper.getMainLooper()).idle()
            Thread.sleep(10)
        }
        server.shutdown()
        app.database.close()
    }

    private fun signIn(role: String) = graph.session.signedIn(Profile("A", "Demo A", role))

    private fun settle(done: () -> Boolean) {
        repeat(300) {
            shadowOf(Looper.getMainLooper()).idle()
            if (done()) return
            Thread.sleep(10)
        }
        error("never settled")
    }

    private fun loaded(model: IdentifyViewModel): ExplainedState {
        model.recommendations.load(customer)
        settle { !model.recommendations.state.value.loading }
        return model.recommendations.state.value
    }

    private fun rows(operation: Operation): List<OutboxRow> =
        runBlocking { graph.outbox.pendingFor("A") }.filter { it.operation == operation }

    private fun ranks(operation: Operation): List<Int> = rows(operation).map {
        Json.parseToJsonElement(it.body).jsonObject.getValue("rank").jsonPrimitive.int
    }

    /** The part as the profile shows it, in a viewport short enough that the list scrolls. */
    private fun shelf(model: IdentifyViewModel, tester: StateRestorationTester? = null) {
        val content = @androidx.compose.runtime.Composable {
            MosaiqTheme(darkTheme = false) {
                val state by model.recommendations.state.collectAsStateWithLifecycle()
                val records = model.recommendations.records()
                Column(Modifier.height(320.dp).verticalScroll(rememberScrollState())) {
                    RecommendationsPart(
                        state,
                        onRetry = {},
                        onShown = { model.recommendations.shown(it) },
                        onTap = if (records) ({ model.recommendations.tapped(it) }) else null
                    )
                }
            }
        }
        if (tester != null) tester.setContent(content) else compose.setContent(content)
        compose.waitForIdle()
        settle { true }
    }

    @Test
    fun itAsksAtTheCheckedInStoreAndNamesNoChannel() {
        loaded(IdentifyViewModel(graph))
        val url = services.asked.single().requestUrl!!
        assertEquals(customer, url.queryParameter("customer_id"))
        assertEquals("5", url.queryParameter("store_id"))
        assertNull("the channel is never defaulted", url.queryParameter("channel_id"))
    }

    @Test
    fun withNoStoreCheckedInNothingIsAskedAndItSaysSo() {
        graph.session.signedOut()
        signIn("SALES_ASSOCIATE")
        val state = loaded(IdentifyViewModel(graph))
        assertTrue(state.noStore)
        assertTrue(services.asked.isEmpty())
    }

    @Test
    fun eachItemSaysWhereItCanBeHadAndWhyAndAnAlternativeIsMarked() {
        val items = loaded(IdentifyViewModel(graph)).items
        assertEquals(listOf("primary", "primary", "alternative", "elsewhere"), items.map { it.item.kind })

        val first = items[0]
        assertEquals("Item 120", first.name)
        assertEquals("Available here: 22 units on hand", first.availability)
        assertNull(first.note)
        assertEquals(listOf("9 other customers in the same segment bought it"), first.reasons)

        val alternative = items[2]
        assertTrue(alternative.alternative)
        assertEquals("Offered instead of Item 245, which is out of stock here.", alternative.note)
        assertEquals(listOf("A declared substitute for Item 245"), alternative.reasons)

        val elsewhere = items[3]
        assertTrue(elsewhere.elsewhere)
        assertEquals("Not available here. 6 units on hand at Del Valle", elsewhere.availability)
        assertEquals("This store: out of stock. Point the customer to the other store.", elsewhere.note)

        shelf(IdentifyViewModel(graph).also { loaded(it) })
        compose.onNodeWithText("Alternative").assertExists()
        compose.onNodeWithText("At another store").assertExists()
    }

    @Test
    fun anEmptyResultStatesTheServicesReason() {
        val reasons =
            mapOf(
                "none_match" to "Nothing that matches this customer can be sold at this store right now.",
                "PERSONALIZATION_WITHDRAWN" to "No recommendations: the customer objected to personalization.",
                "no_open_assignment" to "No recommendations: the customer has no open segment assignment.",
                "unassigned_result" to "No recommendations: the latest segmentation run left the customer unassigned."
            )
        reasons.forEach { (status, words) ->
            services.answer = ShelfServices.empty(status)
            val model = IdentifyViewModel(graph)
            assertEquals(status, words, loaded(model).whyNone)
        }
        shelf(IdentifyViewModel(graph).also { loaded(it) })
        compose.onNodeWithTag("reco-why-none").assertExists()
    }

    @Test
    fun oneImpressionPerItemReallyShownThroughScrollAndRotation() {
        services.answer = ShelfServices.many(8)
        val model = IdentifyViewModel(graph)
        loaded(model)
        val tester = StateRestorationTester(compose)
        shelf(model, tester)

        // Only what is on screen counts; the end of the list has not been seen yet.
        val first = ranks(Operation.RECO_IMPRESSION)
        assertTrue("something is on screen: $first", first.isNotEmpty())
        assertFalse("the last item is not on screen yet: $first", 8 in first)
        assertEquals(first.distinct(), first)

        // Scrolling down shows the rest, scrolling back up shows the first ones again.
        compose.onNodeWithTag("reco-8").performScrollTo()
        compose.waitForIdle()
        settle { 8 in ranks(Operation.RECO_IMPRESSION) }
        compose.onNodeWithTag("reco-1").performScrollTo()
        compose.waitForIdle()
        settle { true }
        // Recomposition and a rotation: the composition is rebuilt, the model is not.
        tester.emulateSavedInstanceStateRestore()
        compose.waitForIdle()
        settle { true }

        val all = ranks(Operation.RECO_IMPRESSION)
        assertEquals("one impression per item, never two: $all", (1..8).toList(), all.sorted())
        val body = Json.parseToJsonElement(rows(Operation.RECO_IMPRESSION).first().body).jsonObject
        assertEquals("RECO_IMPRESSION", body.getValue("type").jsonPrimitive.content)
        assertEquals("LOYAL_HIGH_VALUE", body.getValue("label_code").jsonPrimitive.content)
        assertEquals(customer, body.getValue("customer_id").jsonPrimitive.content)
        assertEquals(5, body.getValue("store_id").jsonPrimitive.int)
        assertTrue(rows(Operation.RECO_IMPRESSION).all { it.path == "api/engagement/v2/engagement-events" })
        assertTrue("showing is not clicking", rows(Operation.RECO_CLICK).isEmpty())
    }

    @Test
    fun theSameDecisionShownAgainIsNotASecondImpression() {
        val model = IdentifyViewModel(graph)
        val item = loaded(model).items.first()
        assertTrue(model.recommendations.shown(item))
        assertFalse(model.recommendations.shown(item))
        // The same answer again (the service's cache gives the same decision id).
        loaded(model)
        assertFalse(model.recommendations.shown(model.recommendations.state.value.items.first()))
        settle { rows(Operation.RECO_IMPRESSION).isNotEmpty() }
        assertEquals(1, rows(Operation.RECO_IMPRESSION).size)
    }

    @Test
    fun aTapIsASeparateClickEachTime() {
        val model = IdentifyViewModel(graph)
        loaded(model)
        shelf(model)
        settle { rows(Operation.RECO_IMPRESSION).isNotEmpty() }
        val impressions = rows(Operation.RECO_IMPRESSION).size

        compose.onNodeWithTag("reco-1").onChildAt(0).assertHasClickAction().performClick()
        compose.onNodeWithTag("reco-1").onChildAt(0).performClick()
        settle { rows(Operation.RECO_CLICK).size == 2 }

        val clicks = rows(Operation.RECO_CLICK)
        assertEquals(listOf(1, 1), ranks(Operation.RECO_CLICK))
        assertEquals(2, clicks.map { it.clientId }.distinct().size)
        assertEquals(impressions, rows(Operation.RECO_IMPRESSION).size)
        compose.onNodeWithText("Interest in Item 120 recorded.").assertExists()
    }

    @Test
    fun aRoleThatRecordsNothingSeesTheListAndQueuesNothing() {
        signIn("LOYALTY_OFFICER")
        val model = IdentifyViewModel(graph)
        val items = loaded(model).items
        assertEquals(4, items.size)
        shelf(model)
        compose.onNodeWithTag("reco-1").onChildAt(0).assertHasNoClickAction()
        assertFalse(model.recommendations.shown(items.first()))
        assertFalse(model.recommendations.tapped(items.first()))
        settle { true }
        assertTrue(runBlocking { graph.outbox.pendingFor("A") }.isEmpty())
    }
}
