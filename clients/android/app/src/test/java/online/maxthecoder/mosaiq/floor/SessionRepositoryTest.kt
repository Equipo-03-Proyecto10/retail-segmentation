package online.maxthecoder.mosaiq.floor

import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.auth.InMemoryTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionResult
import online.maxthecoder.mosaiq.floor.auth.Tokens
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class SessionRepositoryTest {
    private val server = MockWebServer()
    private val seen = CopyOnWriteArrayList<RecordedRequest>()
    private var routes: (RecordedRequest) -> MockResponse = { MockResponse().setResponseCode(404) }

    @Before
    fun start() {
        server.dispatcher =
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    seen += request
                    return routes(request)
                }
            }
        server.start()
    }

    @After
    fun stop() {
        server.shutdown()
    }

    private fun client(store: InMemoryTokenStore = InMemoryTokenStore()) = Client(server.url("/").toString(), store)

    @Test
    fun signInStoresTheTokensThenReadsWhoIsSignedIn() = runBlocking {
        routes = {
            when (it.path) {
                SIGN_IN -> ok(201, tokens("access-1", "refresh-1"))
                ME -> ok(200, user("STORE_MANAGER"))
                else -> MockResponse().setResponseCode(404)
            }
        }
        val client = client()

        val result = client.repository.signIn(" user3@mosaiq-demo.com ", "Password123!")

        assertEquals("Demo User 3", (result as SessionResult.SignedIn).user.name)
        assertEquals("STORE_MANAGER", result.user.roleCode)
        assertEquals(Tokens("access-1", "refresh-1"), client.store.read())
        val signIn = seen.first { it.path == SIGN_IN }
        assertTrue(signIn.body.readUtf8().contains("\"email\":\"user3@mosaiq-demo.com\""))
        assertNull(signIn.getHeader("Authorization"))
        val me = seen.first { it.path == ME }
        assertEquals("Bearer access-1", me.getHeader("Authorization"))
        for (request in seen) {
            assertEquals("application/json", request.getHeader("Accept"))
            assertNotNull(request.getHeader("X-Correlation-ID"))
        }
        assertEquals(
            "every request has its own correlation id",
            seen.size,
            seen.map {
                it.getHeader("X-Correlation-ID")
            }.toSet().size
        )
    }

    @Test
    fun aRoleOffTheFloorIsTurnedAwayAndSignedOutAtOnce() = runBlocking {
        routes = {
            when (it.path) {
                SIGN_IN -> ok(201, tokens("access-1", "refresh-1"))
                ME -> ok(200, user("ANALYST"))
                CURRENT_SESSION -> MockResponse().setResponseCode(204)
                else -> MockResponse().setResponseCode(404)
            }
        }
        val client = client()

        val result = client.repository.signIn("user2@mosaiq-demo.com", "Password123!")

        assertEquals(SessionResult.RoleRefused("ANALYST"), result)
        assertNull(client.store.read())
        val signOut = seen.single { it.path == CURRENT_SESSION }
        assertEquals("DELETE", signOut.method)
        assertEquals("Bearer access-1", signOut.getHeader("Authorization"))
    }

    @Test
    fun wrongCredentialsShowTheServicesMessage() = runBlocking {
        routes = { error(401, "invalid_credentials", "The email or password is incorrect.") }

        val result = client().repository.signIn("user3@mosaiq-demo.com", "wrong") as SessionResult.Failed

        assertEquals("invalid_credentials", result.error.code)
        assertEquals("The email or password is incorrect.", result.error.message)
        assertEquals("corr-invalid_credentials", result.error.correlationId)
    }

    @Test
    fun rateLimitingShowsTheWaitFromRetryAfter() = runBlocking {
        routes = { error(429, "rate_limited").setHeader("Retry-After", "42") }

        val result = client().repository.signIn("user3@mosaiq-demo.com", "wrong") as SessionResult.Failed

        assertEquals("rate_limited", result.error.code)
        assertEquals(42L, result.error.retryAfterSeconds)
        assertTrue(result.error.message.contains("42 seconds"))
    }

    @Test
    fun signOutEndsTheSessionAtAuthAndForgetsTheTokens() = runBlocking {
        routes = { MockResponse().setResponseCode(204) }
        val client = client(InMemoryTokenStore(Tokens("access-1", "refresh-1")))

        client.repository.signOut()

        val request = seen.single()
        assertEquals("DELETE", request.method)
        assertEquals(CURRENT_SESSION, request.path)
        assertNull(client.store.read())
    }

    @Test
    fun signOutForgetsTheTokensEvenWhenAuthCannotBeReached() = runBlocking {
        val client = client(InMemoryTokenStore(Tokens("access-1", "refresh-1")))
        server.shutdown()

        client.repository.signOut()

        assertNull(client.store.read())
    }

    @Test
    fun withoutANetworkTheFloorStaysOpenAsTheLastConfirmedUser() = runBlocking {
        routes = {
            when (it.path) {
                SIGN_IN -> ok(201, tokens("access-1", "refresh-1"))
                ME -> ok(200, user("STORE_MANAGER"))
                else -> MockResponse().setResponseCode(404)
            }
        }
        val client = client()
        client.repository.signIn("user3@mosaiq-demo.com", "Password123!")
        server.shutdown()

        val offline = client.repository.currentUser() as SessionResult.SignedIn

        assertEquals("Demo User 3", offline.user.name)
        assertEquals("STORE_MANAGER", offline.user.roleCode)
        assertEquals(Tokens("access-1", "refresh-1"), client.store.read())
    }

    @Test
    fun withoutANetworkOrASessionItIsStillUnreachable() = runBlocking {
        server.shutdown()
        val result = client().repository.currentUser() as SessionResult.Failed
        assertEquals("unreachable", result.error.code)
    }
}
