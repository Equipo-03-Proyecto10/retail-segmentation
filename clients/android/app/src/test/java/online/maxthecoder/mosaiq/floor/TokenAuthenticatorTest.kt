package online.maxthecoder.mosaiq.floor

import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.auth.InMemoryTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionResult
import online.maxthecoder.mosaiq.floor.auth.Tokens
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class TokenAuthenticatorTest {
    private val server = MockWebServer()
    private val refreshes = AtomicInteger()
    private var refreshAnswer: () -> MockResponse = { ok(200, tokens("fresh-access", "fresh-refresh")) }

    @Before
    fun start() {
        server.dispatcher =
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                    REFRESH -> {
                        refreshes.incrementAndGet()
                        // Slow enough that every racing call is already waiting.
                        Thread.sleep(200)
                        refreshAnswer()
                    }
                    ME ->
                        if (request.getHeader("Authorization") == "Bearer fresh-access") {
                            ok(200, user("STORE_MANAGER"))
                        } else {
                            error(401, "invalid_token")
                        }
                    else -> MockResponse().setResponseCode(404)
                }
            }
        server.start()
    }

    @After
    fun stop() {
        server.shutdown()
    }

    private fun expiredClient() = Client(
        server.url("/").toString(),
        InMemoryTokenStore(Tokens("stale-access", "refresh-1"))
    )

    @Test
    fun fiveConcurrentExpiredCallsShareOneRefresh() = runBlocking {
        val client = expiredClient()

        val answers =
            (1..5)
                .map { async(Dispatchers.IO) { client.network.users.getCurrentUser() } }
                .awaitAll()

        assertTrue(answers.all { it.code() == 200 })
        assertEquals("exactly one refresh", 1, refreshes.get())
        assertEquals(Tokens("fresh-access", "fresh-refresh"), client.store.read())
        val refresh = server.takeRequestsMatching(REFRESH).single()
        assertTrue(refresh.body.readUtf8().contains("\"refresh_token\":\"refresh-1\""))
        assertNull("refresh carries no bearer token", refresh.getHeader("Authorization"))
    }

    @Test
    fun aReusedRefreshTokenErasesTheSessionWithAReason() = runBlocking {
        refreshAnswer = { error(401, "refresh_reused") }
        val client = expiredClient()
        // Listening before the call, as the screen does: the event is not replayed.
        val event = async(start = CoroutineStart.UNDISPATCHED) { client.events.signedOut.first() }

        val result = client.repository.currentUser() as SessionResult.Failed
        val signedOut = withTimeout(1_000) { event.await() }

        assertEquals("refresh_reused", result.error.code)
        assertNull(client.store.read())
        assertTrue(signedOut.reason.contains("Sign in again"))
    }

    @Test
    fun aRevokedSessionErasesTheTokens() = runBlocking {
        refreshAnswer = { error(401, "session_revoked") }
        val client = expiredClient()

        client.repository.currentUser()

        assertNull(client.store.read())
    }

    @Test
    fun aServiceThatFindsTheSessionRevokedSignsOutWithoutRefreshing() = runBlocking {
        val client = expiredClient()
        server.dispatcher =
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = error(401, "session_revoked")
            }

        client.repository.currentUser()

        assertNull(client.store.read())
        assertEquals(0, refreshes.get())
    }

    @Test
    fun aSessionRevokedOnTheRetryAfterARefreshSignsOut() = runBlocking {
        server.dispatcher =
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                    REFRESH -> {
                        refreshes.incrementAndGet()
                        ok(200, tokens("fresh-access", "fresh-refresh"))
                    }
                    // Revoked between the refresh and the retry.
                    ME ->
                        if (request.getHeader("Authorization") == "Bearer fresh-access") {
                            error(401, "session_revoked")
                        } else {
                            error(401, "invalid_token")
                        }
                    else -> MockResponse().setResponseCode(404)
                }
            }
        val client = expiredClient()
        val event = async(start = CoroutineStart.UNDISPATCHED) { client.events.signedOut.first() }

        // The raw call, not the repository: only the authenticator can end it.
        val answer = async(Dispatchers.IO) { client.network.users.getCurrentUser() }.await()
        val signedOut = withTimeout(1_000) { event.await() }

        assertEquals(401, answer.code())
        assertEquals(1, refreshes.get())
        assertNull(client.store.read())
        assertTrue(signedOut.reason.isNotBlank())
    }

    @Test
    fun authUnavailableKeepsTheTokensAndSaysTryAgain() = runBlocking {
        refreshAnswer = { error(503, "auth_unavailable") }
        val client = expiredClient()

        val result = client.repository.currentUser() as SessionResult.Failed

        assertEquals("auth_unavailable", result.error.code)
        assertEquals("Authentication is unavailable. Try again.", result.error.message)
        assertEquals(Tokens("stale-access", "refresh-1"), client.store.read())
    }

    @Test
    fun databaseUnavailableKeepsTheTokensToo() = runBlocking {
        refreshAnswer = { error(503, "database_unavailable") }
        val client = expiredClient()

        val result = client.repository.currentUser() as SessionResult.Failed

        assertEquals("database_unavailable", result.error.code)
        assertEquals("The service is unavailable. Try again.", result.error.message)
        assertEquals(Tokens("stale-access", "refresh-1"), client.store.read())
    }

    private fun MockWebServer.takeRequestsMatching(path: String): List<RecordedRequest> =
        (0 until requestCount).mapNotNull { takeRequest() }.filter { it.path == path }
}
