package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.hasAnyAncestor
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.runBlocking
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.CustomerInsightsViewModel
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.SalesViewModel
import online.maxthecoder.mosaiq.floor.ui.ScanViewModel
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.shell.FloorHome
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class FloorShellTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private lateinit var model: FloorViewModel
    private lateinit var scan: ScanViewModel
    private lateinit var sales: SalesViewModel
    private lateinit var insights: CustomerInsightsViewModel
    private var signedOut = false

    @Before
    fun start() {
        app = ApplicationProvider.getApplicationContext()
        graph = app.build("http://127.0.0.1:9/")
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
        model = FloorViewModel(graph)
        scan = ScanViewModel(graph)
        sales = SalesViewModel(graph)
        insights = CustomerInsightsViewModel(graph)
        // A chosen store, so Check-in shows the store and the picker is a sub-state of it.
        model.chosen.value = ChosenStore(1, "Centro")
    }

    @After
    fun stop() {
        app.database.close()
    }

    private fun content() {
        compose.setContent { Home() }
    }

    @androidx.compose.runtime.Composable
    private fun Home() {
        FloorHome(
            signedIn = SessionState.SignedIn("Demo A", "STORE_MANAGER"),
            model = model,
            scan = scan,
            sales = sales,
            insights = insights,
            onSignOut = { signedOut = true }
        )
    }

    private fun settleUntil(done: () -> Boolean) {
        repeat(TRIES) {
            compose.waitForIdle()
            shadowOf(Looper.getMainLooper()).idle()
            if (done()) return
            Thread.sleep(STEP_MS)
        }
        error("still waiting after ${TRIES * STEP_MS} ms")
    }

    private fun pressBack() {
        compose.runOnUiThread { compose.activity.onBackPressedDispatcher.onBackPressed() }
        compose.waitForIdle()
    }

    private fun backIsEnabled(): Boolean {
        var enabled = false
        compose.runOnUiThread { enabled = compose.activity.onBackPressedDispatcher.hasEnabledCallbacks() }
        return enabled
    }

    @Test
    fun theSelectedSectionSurvivesRecreation() {
        val tester = StateRestorationTester(compose)
        tester.setContent { Home() }
        compose.onNodeWithTag("nav-check-in").assertIsSelected()

        listOf("nav-scan", "nav-sale", "nav-outbox").forEach { tag ->
            compose.onNodeWithTag(tag).performClick()
            compose.onNodeWithTag(tag).assertIsSelected()
            tester.emulateSavedInstanceStateRestore()
            compose.onNodeWithTag(tag).assertIsSelected()
        }
    }

    @Test
    fun theStorePickerAndTheCityFieldSurviveRecreation() {
        val tester = StateRestorationTester(compose)
        tester.setContent { Home() }
        compose.onNodeWithTag("store-chip").performClick()
        compose.onNodeWithText("Pick your store").assertExists()
        compose.onNodeWithTag("nav-check-in").assertIsSelected()
        compose.onNode(hasSetTextAction()).performTextInput("Leon")

        tester.emulateSavedInstanceStateRestore()

        compose.onNodeWithText("Pick your store").assertExists()
        compose.onNode(hasSetTextAction()).assertTextContains("Leon")
    }

    @Test
    fun backFromTheStorePickerReturnsToCheckIn() {
        content()
        compose.onNodeWithTag("store-chip").performClick()
        compose.onNodeWithText("Pick your store").assertExists()
        assertTrue(backIsEnabled())

        pressBack()

        compose.onNodeWithText("Pick your store").assertDoesNotExist()
        compose.onNodeWithText("Check in here").assertExists()
        compose.onNodeWithTag("nav-check-in").assertIsSelected()
    }

    @Test
    fun backFromAnotherSectionReturnsToCheckIn() {
        content()
        compose.onNodeWithTag("nav-sale").performClick()
        compose.onNodeWithTag("nav-sale").assertIsSelected()
        assertTrue(backIsEnabled())

        pressBack()

        compose.onNodeWithTag("nav-check-in").assertIsSelected()
    }

    @Test
    fun backOnCheckInIsLeftToTheSystemSoTheAppLeaves() {
        content()
        compose.onNodeWithTag("nav-check-in").assertIsSelected()
        assertFalse(backIsEnabled())

        // Going away and coming back to Check-in turns the handler off again.
        compose.onNodeWithTag("nav-outbox").performClick()
        compose.onNodeWithTag("nav-outbox").assertIsSelected()
        assertTrue(backIsEnabled())
        pressBack()
        assertFalse(backIsEnabled())
    }

    @Test
    fun theOutboxBadgeShowsThePendingCount() {
        runBlocking {
            graph.writes.queueCheckIn(3, null)
            graph.writes.queueCheckIn(4, null)
        }
        content()
        settleUntil {
            compose.onAllNodesWithTag(
                "outbox-badge",
                useUnmergedTree = true
            ).fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNode(
            hasText("2") and hasAnyAncestor(hasTestTag("outbox-badge")),
            useUnmergedTree = true
        ).assertExists()
    }

    @Test
    fun noBadgeWhenNothingIsPending() {
        content()
        compose.waitForIdle()
        compose.onNodeWithTag("outbox-badge", useUnmergedTree = true).assertDoesNotExist()
    }

    @Test
    fun theAccountMenuShowsWhoIsSignedInAndSignOutStillWarns() {
        runBlocking { graph.writes.queueCheckIn(3, null) }
        content()
        compose.onNodeWithText("MOSAIQ").assertExists()
        compose.onNodeWithTag("account-menu").performClick()
        compose.onNodeWithText("Demo A").assertExists()
        compose.onNodeWithText("STORE_MANAGER").assertExists()

        compose.onNodeWithText("Sign out").performClick()
        settleUntil { compose.onAllNodesWithText("Sign out anyway").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Writes are still waiting", useUnmergedTree = true).assertExists()
        assertFalse(signedOut)

        compose.onNodeWithText("Sign out anyway").performClick()
        compose.waitForIdle()
        assertEquals(true, signedOut)
    }

    private companion object {
        const val TRIES = 200
        const val STEP_MS = 10L
    }
}
