package online.maxthecoder.mosaiq.floor

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.Until
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import online.maxthecoder.mosaiq.floor.auth.InMemoryTokenStore
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.CheckInRequest
import online.maxthecoder.mosaiq.floor.net.Network
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.FloorDatabase
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.InMemorySessionStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.CustomerInsightsViewModel
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.SalesViewModel
import online.maxthecoder.mosaiq.floor.ui.ScanViewModel
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.shell.FloorHome
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Both device capabilities refused at the real system dialog (F17-02, F17-03):
 * a denied camera explains why and offers typing the product id, and a denied
 * location still queues the check-in, without a position.
 */
@DeniedPermissions
@RunWith(AndroidJUnit4::class)
class PermissionDeniedInstrumentedTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    private val device = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())
    private val server = MockWebServer()
    private lateinit var app: SalesTestApplication
    private lateinit var database: FloorDatabase
    private lateinit var graph: AppGraph

    @Before
    fun start() {
        app = ApplicationProvider.getApplicationContext()
        for (permission in PERMISSIONS) {
            assertEquals(
                "$permission must start not granted: install without -g and revoke it first",
                PackageManager.PERMISSION_DENIED,
                app.checkSelfPermission(permission)
            )
        }
        // Nothing on this screen needs an answer; the outbox is never sent here.
        server.dispatcher = object : okhttp3.mockwebserver.Dispatcher() {
            override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest) =
                MockResponse().setResponseCode(404).setBody("{}")
        }
        server.start()
        database = Room.inMemoryDatabaseBuilder(app, FloorDatabase::class.java).build()
        val tokens = InMemoryTokenStore().also { it.write(Tokens("token-A", "refresh-A")) }
        val session =
            InMemorySessionStore().also {
                it.signedIn(Profile("A", "Demo", "STORE_MANAGER"))
                it.choose(ChosenStore(3, "Coyoacan"))
            }
        val events = SessionEvents()
        graph =
            AppGraph(
                tokens,
                session,
                events,
                Network(server.url("/").toString(), tokens, events, 1),
                database.outbox(),
                { null },
                {},
                "Test"
            )
        app.graph = graph
        compose.setContent {
            FloorHome(
                signedIn = SessionState.SignedIn("Demo", "STORE_MANAGER"),
                model = FloorViewModel(graph),
                scan = ScanViewModel(graph),
                sales = SalesViewModel(graph),
                insights = CustomerInsightsViewModel(graph),
                onSignOut = {}
            )
        }
    }

    @After
    fun stop() {
        database.close()
        server.shutdown()
    }

    @Test
    fun aDeniedCameraExplainsWhyAndOffersManualEntry() {
        compose.onNodeWithTag("nav-scan").performClick()
        // The bottom bar's label is also "Scan"; the button is the one that is not the tab.
        compose.onNode(hasText("Scan") and hasClickAction() and !hasTestTag("nav-scan")).performClick()
        refuseAtTheSystemDialog()
        compose.waitUntil(timeoutMillis = 10_000) {
            compose.onAllNodes(hasTestTag("camera-denied")).fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithTag("manual-product-id").assertExists()
        assertEquals(PackageManager.PERMISSION_DENIED, app.checkSelfPermission(Manifest.permission.CAMERA))
    }

    @Test
    fun aDeniedLocationQueuesTheCheckInWithoutAPosition() {
        compose.onNodeWithText("Check in here").performClick()
        refuseAtTheSystemDialog()
        compose.waitUntil(timeoutMillis = 10_000) {
            compose.onAllNodes(hasText("Location is off for this app", substring = true))
                .fetchSemanticsNodes().isNotEmpty()
        }
        val row = checkNotNull(runBlocking { graph.outbox.latestCheckIn("A") }) { "no check-in was queued" }
        val request = json.decodeFromString(CheckInRequest.serializer(), row.body)
        assertEquals(3, request.storeId)
        assertNull(request.latitude)
        assertNull(request.longitude)
        assertNull(request.accuracyM)
    }

    /** The permission controller's own button, whatever its label says. */
    private fun refuseAtTheSystemDialog() {
        val deny =
            device.wait(Until.findObject(By.res(CONTROLLER, "permission_deny_button")), 15_000)
                ?: error("the system permission dialog did not appear")
        deny.click()
        device.wait(Until.gone(By.pkg(CONTROLLER)), 10_000)
    }

    private companion object {
        const val CONTROLLER = "com.android.permissioncontroller"
        val PERMISSIONS =
            listOf(
                Manifest.permission.CAMERA,
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION
            )
    }
}
