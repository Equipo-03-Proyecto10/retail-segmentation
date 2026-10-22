package online.maxthecoder.mosaiq.floor

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.runBlocking
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.SignOutButton
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class SignOutWarningTest {
    @get:Rule val compose = createComposeRule()

    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph

    @Before
    fun start() {
        app = ApplicationProvider.getApplicationContext()
        graph = app.build("http://127.0.0.1:9/")
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn("A")
    }

    @After
    fun stop() {
        app.database.close()
    }

    @Test
    fun signingOutWithAPendingRowWarnsAndKeepsTheRowBoundToItsUser() {
        val id = runBlocking { graph.writes.queueCheckIn(3, null) }
        val model = FloorViewModel(graph)
        var signedOut = false
        compose.setContent {
            SignOutButton(model) {
                graph.tokens.clear()
                graph.session.signedOut()
                signedOut = true
            }
        }

        compose.onNodeWithText("Sign out").performClick()
        compose.onNodeWithText("Writes are still waiting").assertExists()
        compose.onNodeWithText("Sign out anyway").performClick()
        compose.waitForIdle()

        assertEquals(true, signedOut)
        assertNull(graph.session.userId.value)
        assertEquals("A", runBlocking { graph.outbox.find(id)!!.userId })
    }

    @Test
    fun signingOutWithNothingPendingAsksNothing() {
        val model = FloorViewModel(graph)
        var signedOut = false
        compose.setContent { SignOutButton(model) { signedOut = true } }

        compose.onNodeWithText("Sign out").performClick()
        compose.waitForIdle()

        compose.onNodeWithText("Writes are still waiting").assertDoesNotExist()
        assertEquals(true, signedOut)
    }
}
