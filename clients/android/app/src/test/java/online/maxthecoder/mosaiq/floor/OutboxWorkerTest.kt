package online.maxthecoder.mosaiq.floor

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.ListenableWorker
import androidx.work.testing.TestListenableWorkerBuilder
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.SaleRequest
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.outbox.refusalAdvice
import online.maxthecoder.mosaiq.floor.session.Profile
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class OutboxWorkerTest {
    private val server = MockWebServer()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph

    @Before
    fun start() {
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString())
    }

    @After
    fun stop() {
        server.shutdown()
        app.database.close()
    }

    private fun signIn(userId: String, access: String) {
        graph.tokens.write(Tokens(access, "refresh-$userId"))
        graph.session.signedIn(Profile(userId, "Demo $userId", "STORE_MANAGER"))
    }

    private fun work(): ListenableWorker.Result = runBlocking {
        TestListenableWorkerBuilder<OutboxWorker>(app).build().doWork()
    }

    private fun checkIn(reason: String, status: Int = 201) = ok(
        status,
        """{"check_in_id":"c-1","client_check_in_id":"x","user_id":"u","store_id":3,""" +
            """"latitude":null,"longitude":null,"accuracy_m":null,"distance_m":null,"reason":"$reason",""" +
            """"auto_verified":false,"verified":false,"occurred_at":"2026-10-21T12:00:00Z",""" +
            """"recorded_at":"2026-10-21T12:00:01Z"}"""
    )

    @Test
    fun twoNetworkFailuresThenSuccessResendTheSameClientId() = runBlocking {
        signIn("A", "token-A")
        val id = graph.writes.queueCheckIn(3, null)
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        server.enqueue(error(503, "database_unavailable"))
        server.enqueue(checkIn("no_fix"))

        assertEquals(ListenableWorker.Result.retry(), work())
        assertEquals(ListenableWorker.Result.retry(), work())
        assertEquals(ListenableWorker.Result.success(), work())

        // The first attempt was cut before the server read it; the next two arrived.
        val sent =
            generateSequence { server.takeRequest(1, TimeUnit.SECONDS) }
                .mapNotNull { request -> request.body.readUtf8().takeIf { it.isNotEmpty() } }
                .toList()
        val ids = sent.map { Regex("\"client_check_in_id\":\"([^\"]+)\"").find(it)!!.groupValues[1] }
        assertEquals(2, ids.size)
        assertEquals(1, ids.toSet().size)
        assertEquals(graph.outbox.find(id)!!.clientId, ids.first())
        val row = graph.outbox.find(id)!!
        assertEquals(RowState.DONE, row.state)
        assertEquals(3, row.attempts)
    }

    @Test
    fun aRateLimitAndAGatewayErrorAreNotFinalAnswers() = runBlocking {
        signIn("A", "token-A")
        val id = graph.writes.queueCheckIn(3, null)
        server.enqueue(error(429, "rate_limited"))
        server.enqueue(MockResponse().setResponseCode(504).setBody("<html>Gateway Time-out</html>"))
        server.enqueue(checkIn("no_fix"))

        assertEquals(ListenableWorker.Result.retry(), work())
        assertEquals(RowState.PENDING, graph.outbox.find(id)!!.state)
        assertEquals(ListenableWorker.Result.retry(), work())
        assertEquals(RowState.PENDING, graph.outbox.find(id)!!.state)
        assertEquals(ListenableWorker.Result.success(), work())
        assertEquals(RowState.DONE, graph.outbox.find(id)!!.state)
    }

    @Test
    fun conflictsAndRefusalsAreFinalAndKeptWithTheirError() = runBlocking {
        signIn("A", "token-A")
        val conflict = graph.writes.queueCheckIn(3, null)
        val unknown = graph.writes.queueCheckIn(99, null)
        server.enqueue(error(409, "client_check_in_conflict"))
        server.enqueue(error(422, "unknown_store"))

        assertEquals(ListenableWorker.Result.success(), work())

        val first = graph.outbox.find(conflict)!!
        assertEquals(RowState.REFUSED to "client_check_in_conflict", first.state to first.code)
        assertEquals("corr-client_check_in_conflict", first.correlationId)
        assertEquals("unknown_store", graph.outbox.find(unknown)!!.code)
    }

    @Test
    fun everyFinalSaleRefusalIsKeptWithItsCodeAndNeverResent() = runBlocking {
        signIn("A", "token-A")
        val request = json.decodeFromString(
            SaleRequest.serializer(),
            contract("sales/examples/record-sale.request.json")
        )
        val refusals =
            listOf(409 to "stock_unavailable", 409 to "idempotency_key_reused", 409 to "duplicate_product_line") +
                listOf("no_stock_record", "inactive_product", "before_registration", "future_dated").map { 422 to it } +
                listOf("invalid_price", "invalid_quantity", "unknown_product", "unknown_store").map { 422 to it }
        val ids =
            refusals.map {
                graph.writes.queueSale(request.customerId, request.storeId, request.channelId, request.lines)
            }
        refusals.forEach { (status, code) -> server.enqueue(error(status, code)) }

        assertEquals(ListenableWorker.Result.success(), work())
        assertEquals(refusals.size, server.requestCount)
        refusals.zip(ids).forEach { (refusal, id) ->
            val row = graph.outbox.find(id)!!
            assertEquals(RowState.REFUSED, row.state)
            assertEquals(refusal.first, row.status)
            assertEquals(refusal.second, row.code)
            assertEquals(1, row.attempts)
        }
        // Nothing is pending, so a later run sends nothing.
        assertEquals(ListenableWorker.Result.success(), work())
        assertEquals(refusals.size, server.requestCount)
        assertTrue(refusalAdvice("future_dated")!!.contains("phone's clock"))
        assertEquals(null, refusalAdvice("stock_unavailable"))
    }

    @Test
    fun a401KeepsTheRowForTheSessionToComeBack() = runBlocking {
        signIn("A", "token-A")
        val id = graph.writes.queueCheckIn(3, null)
        server.enqueue(error(401, "session_revoked"))

        assertEquals(ListenableWorker.Result.success(), work())

        assertEquals(RowState.PENDING, graph.outbox.find(id)!!.state)
    }

    @Test
    fun anotherUsersWriteWaitsThenLeavesWithItsOwnersToken() = runBlocking {
        signIn("A", "token-A")
        val id = graph.writes.queueCheckIn(3, null)
        val original = graph.outbox.find(id)!!.clientId
        // A signs out and B signs in on the same phone.
        graph.tokens.clear()
        graph.session.signedOut()
        signIn("B", "token-B")

        assertEquals(ListenableWorker.Result.success(), work())
        assertEquals("nothing is sent under B's token", 0, server.requestCount)
        assertEquals("A", graph.outbox.find(id)!!.userId)

        graph.tokens.clear()
        graph.session.signedOut()
        signIn("A", "token-A-again")
        server.enqueue(checkIn("no_fix"))

        assertEquals(ListenableWorker.Result.success(), work())
        val request = server.takeRequest()
        assertEquals("Bearer token-A-again", request.getHeader("Authorization"))
        assertTrue(request.body.readUtf8().contains("\"client_check_in_id\":\"$original\""))
        assertEquals(RowState.DONE, graph.outbox.find(id)!!.state)
    }
}
