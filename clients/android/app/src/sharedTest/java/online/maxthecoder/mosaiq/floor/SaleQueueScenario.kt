package online.maxthecoder.mosaiq.floor

import android.app.Application
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.work.ListenableWorker
import androidx.work.testing.TestListenableWorkerBuilder
import java.time.OffsetDateTime
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import online.maxthecoder.mosaiq.floor.auth.InMemoryTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.Network
import online.maxthecoder.mosaiq.floor.net.PageInfo
import online.maxthecoder.mosaiq.floor.net.Sale
import online.maxthecoder.mosaiq.floor.net.SalePage
import online.maxthecoder.mosaiq.floor.net.SaleRequest
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.FloorDatabase
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.InMemorySessionStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.SalesViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** Both runtimes use real Room files, fresh clients and real CoroutineWorkers. */
class SalesTestApplication : Application(), GraphProvider {
    override lateinit var graph: AppGraph
}

abstract class SaleQueueScenario {
    protected abstract fun fixture(path: String): String

    private lateinit var app: SalesTestApplication
    private lateinit var database: FloorDatabase
    private lateinit var graph: AppGraph
    private val server = MockWebServer()
    private val posts = CopyOnWriteArrayList<RecordedRequest>()
    private val recorded = linkedMapOf<String, Sale>()
    private var timeoutFirst = false
    private val databaseName = "sale-queue-scenario.db"

    @Before
    fun start() {
        app = ApplicationProvider.getApplicationContext()
        app.deleteDatabase(databaseName)
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.requestUrl!!.encodedPath
                if (request.method == "POST" && path == "/api/sales/v1/sales") {
                    posts += request
                    val key = request.getHeader("Idempotency-Key")!!
                    val previous = recorded[key]
                    val sale =
                        previous
                            ?: json.decodeFromString(Sale.serializer(), fixture("sales/examples/record-sale.json"))
                    recorded[key] = sale
                    if (timeoutFirst && posts.size == 1) {
                        // The transaction committed, but no response reaches the phone.
                        return MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE)
                    }
                    val body = json.encodeToString(Sale.serializer(), sale)
                    return response(if (previous == null) 201 else 200, body)
                        .apply { if (previous != null) setHeader("Idempotent-Replayed", "true") }
                }
                if (request.method == "GET" && path == "/api/sales/v1/sales") {
                    val customer = request.requestUrl!!.queryParameter("customer_id")
                    val selected = recorded.values.filter { it.customerId == customer }
                    val page = SalePage(selected, PageInfo(1, 20, selected.size))
                    return response(200, json.encodeToString(SalePage.serializer(), page))
                }
                if (request.method == "GET" && path == "/api/sales/v1/sales/4812") {
                    return response(200, fixture("sales/examples/get-sale.json"))
                }
                return response(404, "{}")
            }
        }
        server.start()
        reopen()
    }

    private fun reopen() {
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

    private suspend fun work(): ListenableWorker.Result = TestListenableWorkerBuilder<OutboxWorker>(
        app
    ).build().doWork()

    private suspend fun confirm(): Long {
        val request = json.decodeFromString(
            SaleRequest.serializer(),
            fixture("sales/examples/record-sale.request.json")
        )
        return graph.writes.queueSale(
            request.customerId,
            request.storeId,
            request.channelId,
            request.lines,
            OffsetDateTime.parse(request.occurredAt)
        )
    }

    @Test
    fun offlineConfirmRestartReconnectRecordsExactlyOneSale() = runBlocking {
        // No connected work is run while offline, as the scheduler's CONNECTED constraint requires.
        val id = confirm()
        val original = graph.outbox.find(id)!!
        assertEquals(RowState.PENDING, original.state)
        assertEquals(0, server.requestCount)
        assertEquals(UUID.fromString(original.clientId).toString(), original.clientId)
        assertTrue(original.body.contains("2026-10-15T14:32:10-06:00"))
        database.close()
        reopen()
        assertEquals(original, graph.outbox.find(id))

        assertEquals(ListenableWorker.Result.success(), work())
        assertEquals(ListenableWorker.Result.success(), work())
        assertEquals(1, posts.size)
        assertEquals(original.clientId, posts.single().getHeader("Idempotency-Key"))
        assertEquals("application/json", posts.single().getHeader("Accept"))
        assertEquals(original.body, posts.single().body.clone().readUtf8())
        assertOneSale(id, 201)
    }

    @Test
    fun committedRequestTimesOutAndReplaysTheOriginalSaleAfterRestart() = runBlocking {
        timeoutFirst = true
        val id = confirm()
        val key = graph.outbox.find(id)!!.clientId
        assertEquals(ListenableWorker.Result.retry(), work())
        assertEquals(RowState.PENDING, graph.outbox.find(id)!!.state)
        assertEquals(1, recorded.size)
        database.close()
        reopen()

        assertEquals(ListenableWorker.Result.success(), work())
        assertEquals(ListenableWorker.Result.success(), work())
        assertEquals(2, posts.size)
        assertEquals(listOf(key, key), posts.map { it.getHeader("Idempotency-Key") })
        assertEquals(posts[0].body.clone().readUtf8(), posts[1].body.clone().readUtf8())
        val row = graph.outbox.find(id)!!
        assertEquals("idempotent_replayed", row.code)
        assertTrue(SalesViewModel(graph).status(row)!!.contains("same sale"))
        assertOneSale(id, 200)
    }

    private suspend fun assertOneSale(id: Long, status: Int) {
        val row = graph.outbox.find(id)!!
        assertEquals(RowState.DONE, row.state)
        assertEquals(status, row.status)
        val customerId = json.decodeFromString(SaleRequest.serializer(), row.body).customerId
        val list = graph.network.sales.listSales(customerId).body()!!
        assertEquals(1, list.pageInfo.total)
        assertEquals(listOf(4812L), list.sales.map { it.saleId })
        assertEquals(4812L, graph.network.sales.getSale(4812).body()!!.saleId)
        // This exact Room flow backs the screen, including after process restart.
        assertEquals(listOf(id), graph.outbox.observeSales("A").first().map { it.id })
    }

    private fun response(status: Int, body: String) = MockResponse().setResponseCode(
        status
    ).setHeader("Content-Type", "application/json").setBody(body)
}
