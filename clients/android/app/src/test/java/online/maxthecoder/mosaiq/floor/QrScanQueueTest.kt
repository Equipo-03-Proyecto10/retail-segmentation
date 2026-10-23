package online.maxthecoder.mosaiq.floor

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.ListenableWorker
import androidx.work.testing.TestListenableWorkerBuilder
import java.time.Instant
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.scan.ScanSession
import online.maxthecoder.mosaiq.floor.session.Profile
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** One QR_SCAN per decoded code, resent with the same client_event_id (F17-02). */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class QrScanQueueTest {
    private val server = MockWebServer()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private val now = Instant.parse("2026-10-21T12:00:00Z")

    @Before
    fun start() {
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString())
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
    }

    @After
    fun stop() {
        server.shutdown()
        app.database.close()
    }

    private fun work(): ListenableWorker.Result = runBlocking {
        TestListenableWorkerBuilder<OutboxWorker>(app).build().doWork()
    }

    private fun recorded() = ok(
        201,
        """{"event_id":"e-1","client_event_id":"x","type":"QR_SCAN","store_id":3,"product_id":120,""" +
            """"customer_id":null,"label_code":null,"rank":null,"recorded_by":"A",""" +
            """"occurred_at":"2026-10-21T12:00:00Z","recorded_at":"2026-10-21T12:00:01Z",""" +
            """"voided":false,"history":[]}"""
    )

    private fun clientEventIds(): List<String> = generateSequence { server.takeRequest(1, TimeUnit.SECONDS) }
        .mapNotNull { request -> request.body.readUtf8().takeIf { it.isNotEmpty() } }
        .map { Regex("\"client_event_id\":\"([^\"]+)\"").find(it)!!.groupValues[1] }
        .toList()

    @Test
    fun oneDecodedCodeIsOneQrScanResentWithTheSameIdAcrossTwoFailures() = runBlocking {
        val session = ScanSession()
        val decoded = (0 until 20).mapNotNull { session.onDecoded("120", now.plusMillis(it * 33L)) }
        assertEquals(1, decoded.size)
        val id = graph.writes.queueQrScan(decoded.single(), 120, 3)

        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        server.enqueue(error(503, "database_unavailable"))
        server.enqueue(recorded())
        assertEquals(ListenableWorker.Result.retry(), work())
        assertEquals(ListenableWorker.Result.retry(), work())
        assertEquals(ListenableWorker.Result.success(), work())

        val ids = clientEventIds()
        assertEquals(2, ids.size) // the first attempt was cut before the server read it
        assertEquals(setOf(decoded.single().clientEventId), ids.toSet())
        val row = graph.outbox.find(id)!!
        assertEquals(Operation.QR_SCAN, row.operation)
        assertEquals("api/engagement/v1/engagement-events", row.path)
        assertEquals(RowState.DONE, row.state)
        assertEquals(3, row.attempts)
    }

    @Test
    fun theEventCarriesStoreProductTimeAndNoCustomerYet() = runBlocking {
        val scan = ScanSession(newId = { "0e6d5c4b-3a29-4817-96f5-a4b3c2d1e0f1" }).onDecoded("120", now)!!
        graph.writes.queueQrScan(scan, 120, 3)
        server.enqueue(recorded())
        assertEquals(ListenableWorker.Result.success(), work())

        val body = server.takeRequest(1, TimeUnit.SECONDS)!!.body.readUtf8()
        assertEquals(
            """{"client_event_id":"0e6d5c4b-3a29-4817-96f5-a4b3c2d1e0f1","type":"QR_SCAN",""" +
                """"store_id":3,"product_id":120,"occurred_at":"2026-10-21T12:00:00Z"}""",
            body
        )
    }

    @Test
    fun aClientEventConflictIsRefusedAndKept() = runBlocking {
        val scan = ScanSession().onDecoded("120", now)!!
        val id = graph.writes.queueQrScan(scan, 120, 3)
        server.enqueue(error(409, "client_event_conflict"))
        assertEquals(ListenableWorker.Result.success(), work())
        val row = graph.outbox.find(id)!!
        assertEquals(RowState.REFUSED, row.state)
        assertEquals("client_event_conflict", row.code)
    }
}
