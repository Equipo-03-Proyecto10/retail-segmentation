package online.maxthecoder.mosaiq.floor

import androidx.test.core.app.ApplicationProvider
import androidx.work.ListenableWorker
import androidx.work.testing.TestListenableWorkerBuilder
import java.io.File
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.Profile
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/** Engagement's final refusals of a RECO_CLICK are listed in the outbox and never retried (F17-05). */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [35], application = FloorTestApp::class)
class RecoClickRefusalTest(private val example: String, private val status: Int, private val code: String) {
    private val server = MockWebServer()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph

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

    @Test
    fun theRefusalIsKeptInTheOutboxAndNeverRetried() = runBlocking {
        val id = graph.writes.queueRecoClick("3f6c1d52-8e4a-4b7d-a0c9-5d2e7b1f4a68", 3, 245, "LOYAL_HIGH_VALUE", 2)
        val body = File("../../../contracts/engagement/examples/$example.json").readText()
        server.enqueue(
            MockResponse().setResponseCode(status).setHeader("Content-Type", "application/json").setBody(body)
        )

        assertEquals(ListenableWorker.Result.success(), work())
        assertEquals(ListenableWorker.Result.success(), work())

        assertEquals(1, server.requestCount)
        val row = graph.outbox.find(id)!!
        assertEquals(RowState.REFUSED, row.state)
        assertEquals(code, row.code)
        assertEquals(1, row.attempts)
        val listed = graph.outbox.observeOpen().first()
        assertEquals(listOf(Operation.RECO_CLICK), listed.map { it.operation })
    }

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{2}")
        fun refusals(): List<Array<Any>> = listOf(
            arrayOf("client-event-conflict", 409, "client_event_conflict"),
            arrayOf("unknown-store", 422, "unknown_store"),
            arrayOf("unknown-product", 422, "unknown_product"),
            arrayOf("unknown-customer", 422, "unknown_customer"),
            arrayOf("occurred-in-future", 422, "occurred_in_future")
        )
    }
}
