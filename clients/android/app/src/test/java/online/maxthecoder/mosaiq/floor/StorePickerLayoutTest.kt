package online.maxthecoder.mosaiq.floor

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.CustomerInsightsViewModel
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.SalesViewModel
import online.maxthecoder.mosaiq.floor.ui.ScanViewModel
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.shell.FloorHome
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** UX-03: on a 375dp screen the store search keeps its button on one line, beside the City field. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class StorePickerLayoutTest {
    @get:Rule val compose = createComposeRule()

    private lateinit var app: FloorTestApp

    @Before
    fun start() {
        app = ApplicationProvider.getApplicationContext()
        val graph = app.build("http://127.0.0.1:9/")
        graph.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
    }

    @After
    fun stop() {
        app.database.close()
    }

    @Test
    fun theSearchButtonKeepsItsOwnWidthNextToTheCityField() {
        val graph = app.graph
        compose.setContent {
            Box(Modifier.width(375.dp)) {
                FloorHome(
                    signedIn = SessionState.SignedIn("Demo A", "STORE_MANAGER"),
                    model = FloorViewModel(graph),
                    scan = ScanViewModel(graph),
                    sales = SalesViewModel(graph),
                    insights = CustomerInsightsViewModel(graph),
                    onSignOut = {}
                )
            }
        }
        // The shell opens on Check-in; with no store chosen the picker is one tap away, on the store chip.
        compose.onNodeWithTag("store-chip").performClick()
        val bounds = compose.onNode(hasClickAction() and hasText("Search")).getUnclippedBoundsInRoot()
        val width = bounds.right - bounds.left
        // The button's own padding is 24dp a side; under that the label has no room (the squeezed layout is 32dp).
        assertTrue("Search is squeezed to $width", width > 48.dp)
    }
}
