package online.maxthecoder.mosaiq.floor

import androidx.activity.ComponentActivity
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import online.maxthecoder.mosaiq.floor.auth.FloorAccess
import online.maxthecoder.mosaiq.floor.auth.FloorSection
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.CustomerInsightsViewModel
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.PromotionsViewModel
import online.maxthecoder.mosaiq.floor.ui.SalesViewModel
import online.maxthecoder.mosaiq.floor.ui.ScanViewModel
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.shell.FloorHome
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** The real shell, one role at a time: what each profile's bottom bar offers, and nothing it does not hold. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class FloorProfilesTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    private lateinit var app: FloorTestApp
    private lateinit var model: FloorViewModel
    private lateinit var scan: ScanViewModel
    private lateinit var sales: SalesViewModel
    private lateinit var insights: CustomerInsightsViewModel
    private var role by mutableStateOf("STORE_MANAGER")

    private val tags =
        mapOf(
            FloorSection.CHECK_IN to "nav-check-in",
            FloorSection.SCAN to "nav-scan",
            FloorSection.SALE to "nav-sale",
            FloorSection.OUTBOX to "nav-outbox"
        )

    @Before
    fun start() {
        app = ApplicationProvider.getApplicationContext()
        val graph = app.build("http://127.0.0.1:9/")
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
        model = FloorViewModel(graph)
        scan = ScanViewModel(graph)
        sales = SalesViewModel(graph)
        insights = CustomerInsightsViewModel(graph)
        model.chosen.value = ChosenStore(1, "Centro")
    }

    @After
    fun stop() {
        app.database.close()
    }

    @androidx.compose.runtime.Composable
    private fun Home() {
        FloorHome(
            signedIn = SessionState.SignedIn("Demo A", role),
            model = model,
            scan = scan,
            sales = sales,
            insights = insights,
            promotions = PromotionsViewModel(app.graph),
            onSignOut = {}
        )
    }

    private fun assertBarIs(expected: List<FloorSection>) {
        tags.forEach { (section, tag) ->
            val shown = compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
            assertEquals("$role: $tag shown", section in expected, shown)
        }
    }

    @Test
    fun eachFloorRoleGetsExactlyItsOwnBottomNavigation() {
        compose.setContent { Home() }
        listOf("SALES_ASSOCIATE", "STORE_MANAGER", "LOYALTY_OFFICER", "COMMERCIAL_SUPERVISOR", "ADMIN").forEach {
            role = it
            compose.waitForIdle()
            assertBarIs(FloorAccess.sections(it))
            compose.onNodeWithTag("nav-check-in").assertIsSelected()
        }
    }

    @Test
    fun aRoleOffTheFloorSeesAPlainMessageAndNoScreen() {
        role = "ANALYST"
        compose.setContent { Home() }

        compose.onNodeWithTag("turned-away").assertIsDisplayed()
        assertBarIs(emptyList())
        compose.onAllNodesWithTag("store-chip").assertCountEquals(0)
        compose.onAllNodesWithTag("account-menu").assertCountEquals(0)
    }

    @Test
    fun aSavedSaleIsNotRestoredForARoleThatDoesNotHoldIt() {
        val tester = StateRestorationTester(compose)
        tester.setContent { Home() }
        compose.onNodeWithTag("nav-sale").performClick()
        compose.onNodeWithTag("nav-sale").assertIsSelected()

        // The same saved state comes back for a profile with no Sale.
        role = "LOYALTY_OFFICER"
        tester.emulateSavedInstanceStateRestore()

        assertBarIs(FloorAccess.sections("LOYALTY_OFFICER"))
        compose.onNodeWithTag("nav-check-in").assertIsSelected()
    }

    @Test
    fun aRoleChangedUnderTheShellLeavesTheSectionItNoLongerHolds() {
        compose.setContent { Home() }
        compose.onNodeWithTag("nav-scan").performClick()
        compose.onNodeWithTag("nav-scan").assertIsSelected()

        role = "COMMERCIAL_SUPERVISOR"
        compose.waitForIdle()

        compose.onNodeWithTag("nav-check-in").assertIsSelected()
        assertTrue(compose.onAllNodesWithTag("nav-scan").fetchSemanticsNodes().isEmpty())
    }
}
