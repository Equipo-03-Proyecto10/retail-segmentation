package online.maxthecoder.mosaiq.floor

import androidx.compose.material3.Text
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import okhttp3.mockwebserver.MockWebServer
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.CameraPermission
import online.maxthecoder.mosaiq.floor.ui.ScanContent
import online.maxthecoder.mosaiq.floor.ui.ScanViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** The scan screen without a camera: denied permission, manual entry, no store (F17-02). */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class ScanPermissionTest {
    @get:Rule val compose = createComposeRule()

    private val server = MockWebServer()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph

    @Before
    fun start() {
        server.dispatcher = FloorServices()
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString())
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
        graph.session.choose(ChosenStore(3, "Coyoacan"))
    }

    @After
    fun stop() {
        server.shutdown()
        app.database.close()
    }

    @Test
    fun aDeniedCameraExplainsWhyAndOffersManualEntry() {
        val model = ScanViewModel(graph)
        var cameraShown = false
        compose.setContent {
            ScanContent(
                model = model,
                store = ChosenStore(3, "Coyoacan"),
                permission = CameraPermission.DENIED,
                scanning = false,
                onPickStore = {},
                onScan = {},
                camera = {
                    cameraShown = true
                    Text("camera")
                }
            )
        }

        compose.onNodeWithTag("camera-denied").assertExists()
        compose.onNodeWithText("Scan").assertDoesNotExist()
        compose.onNodeWithTag("manual-product-id").performTextInput("120")
        compose.onNodeWithText("Look up").performScrollTo().performClick()
        compose.waitUntil(timeoutMillis = 5_000) {
            compose.onAllNodes(androidx.compose.ui.test.hasText("14 units at Coyoacan")).fetchSemanticsNodes()
                .isNotEmpty()
        }
        compose.onNodeWithText("Item 120").assertExists()
        assertEquals(false, cameraShown)
    }

    @Test
    fun theFirstTapOnScanIsWhatAsksForTheCamera() {
        val model = ScanViewModel(graph)
        var asked = 0
        compose.setContent {
            ScanContent(
                model = model,
                store = ChosenStore(3, "Coyoacan"),
                permission = CameraPermission.NOT_ASKED,
                scanning = false,
                onPickStore = {},
                onScan = { asked++ },
                camera = { Text("camera") }
            )
        }
        assertEquals(0, asked)
        compose.onNodeWithText("Scan").performClick()
        assertEquals(1, asked)
    }

    @Test
    fun withoutAStoreTheScreenAsksForOneFirst() {
        val model = ScanViewModel(graph)
        var picking = false
        compose.setContent {
            ScanContent(
                model = model,
                store = null,
                permission = CameraPermission.GRANTED,
                scanning = false,
                onPickStore = { picking = true },
                onScan = {},
                camera = { Text("camera") }
            )
        }
        compose.onNodeWithText("Pick a store").performClick()
        assertEquals(true, picking)
        compose.onNodeWithTag("manual-product-id").assertDoesNotExist()
    }
}
