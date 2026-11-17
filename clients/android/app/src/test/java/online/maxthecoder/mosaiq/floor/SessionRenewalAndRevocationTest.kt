package online.maxthecoder.mosaiq.floor

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.SaleLineRequest
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.SessionViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

private const val STORES = "/api/stores/v1/stores?active=true&page=1&size=20"
private const val CHECK_INS = "/api/check-ins/v1/check-ins"
private const val SALES = "/api/sales/v1/sales"
private const val USER_ID = "11111111-1111-1111-1111-000000000003"

/**
 * An expired token is renewed once however many calls found it expired, and a revoked session returns to
 * sign-in with the user's queued writes still in the outbox, to be sent after the next sign-in.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class SessionRenewalAndRevocationTest {
    private val server = MockWebServer()
    private val refreshes = AtomicInteger()
    private val seen = CopyOnWriteArrayList<RecordedRequest>()
    @Volatile
    private var revoked = false
    private val bothRejected = java.util.concurrent.CountDownLatch(2)
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph

    @Before
    fun start() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        server.dispatcher =
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    seen += request
                    val path = request.path!!
                    return when {
                        path == REFRESH -> {
                            refreshes.incrementAndGet()
                            // Held until both stale calls were answered 401: a renewal that is not
                            // single-flight would send a second refresh here instead of waiting.
                            bothRejected.await(5, java.util.concurrent.TimeUnit.SECONDS)
                            Thread.sleep(100)
                            ok(200, tokens("fresh-access", "fresh-refresh"))
                        }
                        path == SIGN_IN -> ok(201, tokens("fresh-access", "fresh-refresh"))
                        path == CURRENT_SESSION -> MockResponse().setResponseCode(204)
                        revoked -> error(401, "session_revoked")
                        request.getHeader("Authorization") != "Bearer fresh-access" -> {
                            bothRejected.countDown()
                            error(401, "invalid_token")
                        }
                        path == ME -> ok(200, user("STORE_MANAGER"))
                        path == STORES -> ok(200, """{"stores":[],"page_info":{"page":1,"size":20,"total":0}}""")
                        path == CHECK_INS ->
                            ok(201, """{"check_in_id":"c","store_id":1,"verified":true,"reason":"within_geofence"}""")
                        path == SALES -> ok(201, "{}")
                        else -> MockResponse().setResponseCode(404)
                    }
                }
            }
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString())
    }

    @After
    fun stop() {
        server.shutdown()
        app.database.close()
        Dispatchers.resetMain()
    }

    @Test
    fun twoConcurrentCallsWithAnExpiredTokenShareOneRefreshAndBothSucceed() = runBlocking {
        graph.tokens.write(Tokens("stale-access", "refresh-1"))

        val answers =
            listOf(
                async(Dispatchers.IO) { graph.network.users.getCurrentUser().code() },
                async(Dispatchers.IO) { graph.network.stores.listStores().code() }
            ).awaitAll()

        assertEquals(listOf(200, 200), answers)
        assertEquals("exactly one refresh", 1, refreshes.get())
        assertEquals(Tokens("fresh-access", "fresh-refresh"), graph.tokens.read())
        val retried = seen.filter { it.getHeader("Authorization") == "Bearer fresh-access" }.map { it.path }
        assertEquals(setOf(ME, STORES), retried.toSet())
    }

    @Test
    fun aRevokedSessionReturnsToSignInWithQueuedWorkKeptAndSendsItAfterTheNextSignIn() = runBlocking {
        graph.tokens.write(Tokens("fresh-access", "refresh-1"))
        graph.session.signedIn(Profile(USER_ID, "Demo User 3", "STORE_MANAGER"))
        val checkIn = graph.writes.queueCheckIn(1, null)
        val sale = graph.writes.queueSale("customer-1", 1, 1, listOf(SaleLineRequest(5, 2, "9.99")))
        val before = graph.outbox.pendingFor(USER_ID)
        assertEquals(listOf(checkIn, sale), before.map { it.id })

        revoked = true
        val model = SessionViewModel(graph.sessions, graph.events)
        waitFor { model.state.value is SessionState.SignIn }

        assertTrue("back at sign-in", model.state.value is SessionState.SignIn)
        assertNull("the tokens are gone", graph.tokens.read())
        assertEquals("the outbox is untouched", before, graph.outbox.pendingFor(USER_ID))
        // Nothing is sent while nobody is signed in.
        OutboxWorker.drain(graph.outbox, graph.session, graph.tokens, graph.network.client, graph.network.gateway)
        assertEquals(before, graph.outbox.pendingFor(USER_ID))
        assertTrue(seen.none { it.path == CHECK_INS || it.path == SALES })

        // The same user signs in again: their queued writes go out, each as it was stored.
        revoked = false
        model.signIn("user3@mosaiq-demo.com", "Password123!")
        waitFor { model.state.value is SessionState.SignedIn }
        OutboxWorker.drain(graph.outbox, graph.session, graph.tokens, graph.network.client, graph.network.gateway)

        assertEquals(emptyList<Any>(), graph.outbox.pendingFor(USER_ID))
        assertEquals(RowState.DONE, graph.outbox.find(checkIn)!!.state)
        assertEquals(RowState.DONE, graph.outbox.find(sale)!!.state)
        val sent = seen.filter { it.path == CHECK_INS || it.path == SALES }
        assertEquals(listOf(CHECK_INS, SALES), sent.map { it.path })
        assertEquals(before[1].clientId, sent[1].getHeader("Idempotency-Key"))
    }

    /** Retrofit resumes on OkHttp's threads, so the state settles a moment later. */
    private fun waitFor(condition: () -> Boolean) {
        val deadline = System.currentTimeMillis() + 5_000
        while (!condition()) {
            check(System.currentTimeMillis() < deadline) { "the state never settled" }
            Thread.sleep(10)
        }
    }
}
