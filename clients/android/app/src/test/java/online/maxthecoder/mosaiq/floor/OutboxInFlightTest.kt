package online.maxthecoder.mosaiq.floor

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** The outbox says "Sending" for the row whose request is on the wire, and only while it is. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class OutboxInFlightTest {
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

    @Test
    fun aRowIsInFlightWhileItsRequestIsOpenAndNotBefore() = runBlocking {
        val model = FloorViewModel(graph)
        val id = graph.writes.queueCheckIn(3, null)
        assertTrue(model.sending.value.isEmpty())

        // The server holds the answer until the test lets it go.
        val release = CountDownLatch(1)
        server.dispatcher =
            object : okhttp3.mockwebserver.Dispatcher() {
                override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest): MockResponse {
                    release.await(5, TimeUnit.SECONDS)
                    return ok(201, "{}")
                }
            }
        val worker =
            thread {
                runBlocking {
                    OutboxWorker.drain(
                        graph.outbox,
                        graph.session,
                        graph.tokens,
                        graph.network.client,
                        graph.network.gateway,
                        graph.inFlight
                    )
                }
            }
        val deadline = System.currentTimeMillis() + 5_000
        while (model.sending.value.isEmpty() && System.currentTimeMillis() < deadline) Thread.sleep(10)

        assertEquals(setOf(id), model.sending.value)
        assertEquals("the stored state is unchanged while sending", RowState.PENDING, graph.outbox.find(id)!!.state)

        release.countDown()
        worker.join(5_000)

        assertTrue("nothing in flight once it is answered", model.sending.value.isEmpty())
        assertEquals(RowState.DONE, graph.outbox.find(id)!!.state)
    }

    @Test
    fun aRefusedOrUnreachableRequestIsNoLongerInFlight() = runBlocking {
        val model = FloorViewModel(graph)
        graph.writes.queueCheckIn(3, null)
        server.enqueue(error(422, "validation_failed"))

        OutboxWorker.drain(
            graph.outbox,
            graph.session,
            graph.tokens,
            graph.network.client,
            graph.network.gateway,
            graph.inFlight
        )

        assertTrue(model.sending.value.isEmpty())
    }

    @Test
    fun sendNowKicksTheQueue() {
        var kicked = 0
        var chained = 0
        val kicking = AppGraph(
            graph.tokens,
            graph.session,
            graph.events,
            graph.network,
            graph.outbox,
            graph.location,
            { chained++ },
            "JVM",
            { kicked++ },
            notified = app.database.notified()
        )

        FloorViewModel(kicking).sendNow()

        assertEquals("a manual send starts its own drain", 1, kicked)
        assertEquals("and does not chain behind the backed-off worker", 0, chained)
    }

    @Test
    fun forgettingTheSessionClearsTokensAndProfileWithoutTheNetwork() = runBlocking {
        server.shutdown()

        FloorViewModel(graph).forgetSession()
        val deadline = System.currentTimeMillis() + 5_000
        while (graph.tokens.read() != null && System.currentTimeMillis() < deadline) Thread.sleep(10)

        assertEquals(null, graph.tokens.read())
        assertEquals(null, graph.session.profile())
    }
}
