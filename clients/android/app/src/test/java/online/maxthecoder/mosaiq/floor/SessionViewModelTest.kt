package online.maxthecoder.mosaiq.floor

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.mockwebserver.MockWebServer
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.SessionViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/** Any refusal reaches the screen with its code, its message and its correlation id. */
@OptIn(ExperimentalCoroutinesApi::class)
class SessionViewModelTest {
    private val server = MockWebServer()

    @Before
    fun start() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        server.start()
    }

    @After
    fun stop() {
        server.shutdown()
        Dispatchers.resetMain()
    }

    @Test
    fun aRefusedSignInShowsCodeMessageAndCorrelationId() {
        server.enqueue(error(401, "invalid_credentials", "The email or password is incorrect."))
        val client = Client(server.url("/").toString())
        val model = SessionViewModel(client.repository, client.events)

        model.signIn("user3@mosaiq-demo.com", "wrong")
        waitFor { model.state.value is SessionState.SignIn && (model.state.value as SessionState.SignIn).error != null }

        val shown = (model.state.value as SessionState.SignIn).error!!
        assertEquals("invalid_credentials", shown.code)
        assertEquals("The email or password is incorrect.", shown.message)
        assertEquals("corr-invalid_credentials", shown.correlationId)
    }

    @Test
    fun aStoreManagerSeesTheirNameAndRole() {
        server.enqueue(ok(201, tokens("access-1", "refresh-1")))
        server.enqueue(ok(200, user("STORE_MANAGER")))
        val client = Client(server.url("/").toString())
        val model = SessionViewModel(client.repository, client.events)

        model.signIn("user3@mosaiq-demo.com", "Password123!")
        waitFor { model.state.value is SessionState.SignedIn }

        assertEquals(SessionState.SignedIn("Demo User 3", "STORE_MANAGER"), model.state.value)
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
