package online.maxthecoder.mosaiq.floor

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.work.ListenableWorker
import androidx.work.testing.TestListenableWorkerBuilder
import java.time.Instant
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import online.maxthecoder.mosaiq.floor.auth.InMemoryTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.Network
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.FloorDatabase
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.InMemorySessionStore
import online.maxthecoder.mosaiq.floor.session.Profile
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Before
import org.junit.Test

/**
 * One tap is one RECO_CLICK, however many times the network fails (F17-05).
 * Runs on the JVM (RecoClickQueueTest) and on a device (RecoClickInstrumentedTest),
 * against an engagement service that records once per client_event_id, answering
 * with the contract's own examples.
 */
abstract class RecoClickScenario {
    protected abstract fun fixture(path: String): String

    private lateinit var app: SalesTestApplication
    private lateinit var database: FloorDatabase
    private lateinit var graph: AppGraph
    private val server = MockWebServer()
    private val posts = CopyOnWriteArrayList<RecordedRequest>()

    /** What engagement recorded, by client_event_id. */
    private val recorded = linkedMapOf<String, JsonObject>()

    /** What the next POSTs meet, in order: a lost answer after recording, or a refusal before it. */
    private val plan = mutableListOf<Failure>()

    private enum class Failure { RECORDED_BUT_NO_ANSWER, UNAVAILABLE }
    private val databaseName = "reco-click-scenario.db"
    private val customer = "3f6c1d52-8e4a-4b7d-a0c9-5d2e7b1f4a68"

    @Before
    fun start() {
        app = ApplicationProvider.getApplicationContext()
        app.deleteDatabase(databaseName)
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.requestUrl!!.encodedPath
                if (request.method == "POST" && path == "/api/engagement/v1/engagement-events") {
                    posts += request
                    val failure = plan.removeFirstOrNull()
                    if (failure == Failure.UNAVAILABLE) {
                        return response(503, fixture("engagement/examples/database-unavailable.json"))
                    }
                    val body = json.parseToJsonElement(request.body.clone().readUtf8()).jsonObject
                    val id = body.getValue("client_event_id").jsonPrimitive.content
                    val previous = recorded[id]
                    val event = previous ?: event(body, recorded.size)
                    recorded[id] = event
                    if (failure == Failure.RECORDED_BUT_NO_ANSWER) {
                        // Engagement recorded the event, but no answer reaches the phone.
                        return MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE)
                    }
                    return response(if (previous == null) 201 else 200, event.toString())
                }
                if (request.method == "GET" && path == "/api/engagement/v1/engagement-events") {
                    val type = request.requestUrl!!.queryParameter("type")
                    val who = request.requestUrl!!.queryParameter("customer_id")
                    val events =
                        recorded.values.filter {
                            (type == null || it.getValue("type").jsonPrimitive.content == type) &&
                                (who == null || it["customer_id"]?.jsonPrimitive?.content == who)
                        }
                    return response(
                        200,
                        """{"engagement_events":$events,"page_info":{"page":1,"size":20,"total":${events.size}}}"""
                    )
                }
                return response(404, "{}")
            }
        }
        server.start()
        database = Room.databaseBuilder(app, FloorDatabase::class.java, databaseName).build()
        val tokens = InMemoryTokenStore().also { it.write(Tokens("token-A", "refresh-A")) }
        val session = InMemorySessionStore().also { it.signedIn(Profile("A", "Demo", "STORE_MANAGER")) }
        val events = SessionEvents()
        graph =
            AppGraph(
                tokens,
                session,
                events,
                Network(server.url("/").toString(), tokens, events, 1),
                database.outbox(),
                { null },
                {},
                "Test"
            )
        app.graph = graph
    }

    @After
    fun stop() {
        database.close()
        app.deleteDatabase(databaseName)
        server.shutdown()
    }

    /** The contract's recorded RECO_CLICK, carrying what this request sent. */
    private fun event(body: JsonObject, index: Int): JsonObject {
        val example = json.parseToJsonElement(fixture("engagement/examples/reco-click.json")).jsonObject
        return buildJsonObject {
            example.forEach { (key, value) -> put(key, body[key] ?: value) }
            put("event_id", "00000000-0000-0000-0000-0000000000${10 + index}")
        }
    }

    private suspend fun work(): ListenableWorker.Result = TestListenableWorkerBuilder<OutboxWorker>(
        app
    ).build().doWork()

    private suspend fun tap(rank: Int = 2): Long =
        graph.writes.queueRecoClick(customer, 3, 245, "LOYAL_HIGH_VALUE", rank, Instant.parse("2026-10-15T15:24:10Z"))

    private suspend fun clicks() =
        graph.network.engagement.listEvents(type = "RECO_CLICK", customerId = customer).body()!!.events

    @Test
    fun twoNetworkFailuresBeforeSuccessLeaveExactlyOneRecoClick() = runBlocking {
        plan += listOf(Failure.RECORDED_BUT_NO_ANSWER, Failure.UNAVAILABLE)
        val id = tap()

        assertEquals(ListenableWorker.Result.retry(), work())
        assertEquals(ListenableWorker.Result.retry(), work())
        assertEquals(ListenableWorker.Result.success(), work())
        assertEquals(ListenableWorker.Result.success(), work())

        val row = graph.outbox.find(id)!!
        assertEquals(RowState.DONE, row.state)
        val events = clicks()
        assertEquals(1, events.size)
        val click = events.single()
        assertEquals(row.clientId, click.clientEventId)
        assertEquals(245, click.productId)
        assertEquals(3, click.storeId)
        assertEquals("LOYAL_HIGH_VALUE", click.labelCode)
        assertEquals(2, click.rank)
        // Three attempts reached engagement, all the same request; the third was a replay.
        assertEquals(3, posts.size)
        assertEquals(1, posts.map { it.body.clone().readUtf8() }.toSet().size)
        assertEquals(200, row.status)
    }

    @Test
    fun aSecondTapOnTheSameItemIsASecondClick() = runBlocking {
        val first = tap()
        val second = tap()
        assertEquals(ListenableWorker.Result.success(), work())

        assertNotEquals(graph.outbox.find(first)!!.clientId, graph.outbox.find(second)!!.clientId)
        assertEquals(2, clicks().size)
    }

    private fun response(status: Int, body: String) = MockResponse().setResponseCode(
        status
    ).setHeader("Content-Type", "application/json").setBody(body)
}
