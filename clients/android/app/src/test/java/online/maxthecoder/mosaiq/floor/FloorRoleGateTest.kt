package online.maxthecoder.mosaiq.floor

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.auth.InMemoryTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.SessionRepository
import online.maxthecoder.mosaiq.floor.auth.SessionResult
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.Network
import online.maxthecoder.mosaiq.floor.session.InMemorySessionStore
import online.maxthecoder.mosaiq.floor.session.Profile
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/** Sign-in for each of ADR-0060's roles, and the turning away of every other (ADR-0076). */
class FloorRoleGateTest {
    private val server = MockWebServer()
    private var me: (RecordedRequest) -> MockResponse = { MockResponse().setResponseCode(404) }

    @Before
    fun start() {
        server.dispatcher =
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                    SIGN_IN -> ok(201, tokens("access-1", "refresh-1"))
                    ME -> me(request)
                    CURRENT_SESSION -> MockResponse().setResponseCode(204)
                    else -> MockResponse().setResponseCode(404)
                }
            }
        server.start()
    }

    @After
    fun stop() {
        server.shutdown()
    }

    private fun signInAs(role: String): Pair<SessionResult, Client> = runBlocking {
        me = { ok(200, user(role)) }
        val client = Client(server.url("/").toString())
        client.repository.signIn("user3@mosaiq-demo.com", "Password123!") to client
    }

    @Test
    fun eachOfTheFourProfilesAndTheAdministratorSignsIn() {
        listOf("SALES_ASSOCIATE", "STORE_MANAGER", "LOYALTY_OFFICER", "COMMERCIAL_SUPERVISOR", "ADMIN").forEach {
            val (result, client) = signInAs(it)
            assertEquals(it, (result as SessionResult.SignedIn).user.roleCode)
            assertEquals(Tokens("access-1", "refresh-1"), client.store.read())
        }
    }

    @Test
    fun everyOtherRoleIsTurnedAwayAndSignedOut() {
        listOf("ANALYST", "MARKETING", "INVENTORY_PLANNER", "AUDITOR", "CUSTOMER", "SUPERVISOR", "store_manager", "")
            .forEach {
                val (result, client) = signInAs(it)
                assertEquals(it, SessionResult.RoleRefused(it).role)
                assertEquals(SessionResult.RoleRefused(it), result)
                assertNull("$it keeps no token", client.store.read())
            }
    }

    @Test
    fun aProfileKeptByAnOlderBuildForARoleNoLongerServedOpensNothingOffline() = runBlocking {
        val tokens = InMemoryTokenStore(Tokens("access-1", "refresh-1"))
        val session = InMemorySessionStore().also { it.signedIn(Profile("A", "Demo A", "ANALYST")) }
        val events = SessionEvents()
        val repository =
            SessionRepository(Network("http://127.0.0.1:9/", tokens, events, 2), tokens, "JVM test", session)

        val result = repository.currentUser()

        assertEquals(SessionResult.RoleRefused("ANALYST"), result)
        assertNull(tokens.read())
        assertNull(session.profile())
    }

    @Test
    fun aKnownRoleKeptOfflineStillOpensTheFloor() = runBlocking {
        val tokens = InMemoryTokenStore(Tokens("access-1", "refresh-1"))
        val session = InMemorySessionStore().also { it.signedIn(Profile("A", "Demo A", "SALES_ASSOCIATE")) }
        val events = SessionEvents()
        val repository =
            SessionRepository(Network("http://127.0.0.1:9/", tokens, events, 2), tokens, "JVM test", session)

        val result = repository.currentUser() as SessionResult.SignedIn

        assertEquals("SALES_ASSOCIATE", result.user.roleCode)
    }
}
