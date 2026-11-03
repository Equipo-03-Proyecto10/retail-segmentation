package online.maxthecoder.mosaiq.floor

import android.Manifest
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import okhttp3.mockwebserver.MockWebServer
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.CameraPermission
import online.maxthecoder.mosaiq.floor.ui.ScanBody
import online.maxthecoder.mosaiq.floor.ui.ScanResult
import online.maxthecoder.mosaiq.floor.ui.ScanScreen
import online.maxthecoder.mosaiq.floor.ui.ScanViewModel
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/** Every state the scan screen can be in, driven from plain values, and the permission re-check. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class ScanStatesTest {
    @get:Rule val compose = createComposeRule()

    private val shirt = Product(120, "SKU-120", "Cotton shirt", 4, "12.5", true)
    private val store = ChosenStore(3, "Coyoacan")

    private var added: Product? = null
    private var scanAnother = 0
    private var retried = 0
    private var cameraRetried = 0
    private var settings = 0
    private var grant = 0
    private var pickedStore = 0

    private fun show(
        result: ScanResult = ScanResult.Idle,
        permission: CameraPermission = CameraPermission.GRANTED,
        scanning: Boolean = false,
        cameraError: Boolean = false,
        withStore: Boolean = true,
        camera: @Composable () -> Unit = { Text("camera") }
    ) = compose.setContent {
        MosaiqTheme {
            ScanBody(
                store = if (withStore) store else null,
                permission = permission,
                scanning = scanning,
                result = result,
                onPickStore = { pickedStore++ },
                onScan = {},
                onScanAnother = { scanAnother++ },
                onRetryLookup = { retried++ },
                onLookUp = {},
                onAddProduct = { added = it },
                onOpenSettings = { settings++ },
                onGrantAccess = { grant++ },
                cameraError = cameraError,
                onRetryCamera = { cameraRetried++ },
                camera = camera
            )
        }
    }

    @Test
    fun aProductShowsItsPriceAsCurrencyAndInStockAsASuccessBadge() {
        show(ScanResult.Shown(shirt, "Coyoacan", 14, null, 1L))
        compose.onNodeWithText("Cotton shirt").assertExists()
        compose.onNodeWithText("$12.50").assertExists()
        compose.onNodeWithText("In stock").assertExists()
        compose.onNodeWithText("14 units at Coyoacan").assertExists()
        compose.onNodeWithText("Add to sale").performScrollTo().performClick()
        assertEquals(shirt, added)
        compose.onNodeWithText("Scan another").performScrollTo().performClick()
        assertEquals(1, scanAnother)
    }

    @Test
    fun zeroStockIsOutOfStockWithADangerBadge() {
        show(ScanResult.Shown(shirt, "Coyoacan", 0, null, 1L))
        compose.onNodeWithText("Out of stock").assertExists()
        compose.onNodeWithText("None at Coyoacan").assertExists()
    }

    @Test
    fun oneUnitReadsInTheSingular() {
        show(ScanResult.Shown(shirt, "Coyoacan", 1, null, 1L))
        compose.onNodeWithText("1 unit at Coyoacan").assertExists()
    }

    @Test
    fun noInventoryRowSaysTheStoreDoesNotHoldIt() {
        show(ScanResult.Shown(shirt, "Coyoacan", null, null, 1L))
        compose.onNodeWithText("Not stocked here").assertExists()
        compose.onNodeWithText("Coyoacan does not hold this product.").assertExists()
    }

    @Test
    fun lookingUpShowsALoadingLine() {
        show(ScanResult.Looking)
        compose.onNodeWithText("Looking up the product…").assertExists()
    }

    @Test
    fun aMissingProductIsFriendlyAndOffersAnotherScan() {
        show(ScanResult.ProductNotFound)
        compose.onNodeWithText("We could not find that product").assertExists()
        compose.onNodeWithText("Scan another").performScrollTo().performClick()
        assertEquals(1, scanAnother)
    }

    @Test
    fun anOfflineLookupShowsAnErrorPanelWithRetry() {
        show(ScanResult.Failed(ApiError.unreachable(null)))
        compose.onNodeWithText("The store systems could not be reached", substring = true).assertExists()
        compose.onNodeWithText("Retry").performScrollTo().performClick()
        assertEquals(1, retried)
    }

    @Test
    fun withoutAStoreThePickAStoreActionIsOffered() {
        show(withStore = false)
        compose.onNodeWithText("Pick a store").performScrollTo().performClick()
        assertEquals(1, pickedStore)
    }

    @Test
    fun theCameraViewportIsDescribedForTalkBackAndTheHintShows() {
        show(scanning = true)
        compose.onNode(hasContentDescription("Camera viewport for scanning a product QR code")).assertExists()
        compose.onNodeWithText("Point at a product QR code").assertExists()
        compose.onNodeWithText("camera").assertExists()
    }

    @Test
    fun anAnswerReplacesTheCameraSoThePreviewStops() {
        show(ScanResult.ProductNotFound, scanning = true)
        compose.onNodeWithText("camera").assertDoesNotExist()
    }

    @Test
    fun aCameraThatCannotStartShowsAnErrorWithRetryInsteadOfAPreview() {
        show(scanning = true, cameraError = true)
        compose.onNodeWithTag("camera-error").assertExists()
        compose.onNodeWithText("camera").assertDoesNotExist()
        compose.onNodeWithText("Retry").performScrollTo().performClick()
        assertEquals(1, cameraRetried)
    }

    @Test
    fun aDeniedCameraOffersGrantAccessAndOpenSettings() {
        show(permission = CameraPermission.DENIED)
        compose.onNodeWithText("Grant access").performScrollTo().performClick()
        compose.onNodeWithText("Open settings").performScrollTo().performClick()
        assertEquals(1, grant)
        assertEquals(1, settings)
    }

    @Test
    fun theLookUpButtonWaitsForAProductNumber() {
        show()
        compose.onNodeWithText("Look up").assertIsNotEnabled()
    }
}

/** The permission is asked again on every resume (UX-08): a revoke in Settings shows the denied state. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class ScanPermissionRecheckTest {
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
    fun aCameraRevokedInSettingsShowsTheDeniedStateOnResume() {
        shadowOf(app).grantPermissions(Manifest.permission.CAMERA)
        val owner = TestOwner()
        compose.setContent {
            CompositionLocalProvider(LocalLifecycleOwner provides owner) {
                MosaiqTheme { ScanScreen(ScanViewModel(graph), ChosenStore(3, "Coyoacan"), onPickStore = {}) }
            }
        }
        compose.runOnUiThread { owner.moveTo(Lifecycle.State.RESUMED) }
        compose.onNodeWithTag("camera-denied").assertDoesNotExist()
        compose.onNodeWithText("Scan").assertExists()

        shadowOf(app).denyPermissions(Manifest.permission.CAMERA)
        compose.runOnUiThread {
            owner.moveTo(Lifecycle.State.STARTED)
            owner.moveTo(Lifecycle.State.RESUMED)
        }
        compose.waitUntil(timeoutMillis = 5_000) {
            compose.onAllNodes(hasTestTag("camera-denied")).fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithText("Open settings").assertExists()
        compose.onNodeWithText("Grant access").assertExists()
    }

    private class TestOwner : LifecycleOwner {
        private val registry = LifecycleRegistry.createUnsafe(this)
        override val lifecycle: Lifecycle get() = registry

        fun moveTo(state: Lifecycle.State) {
            registry.currentState = state
        }
    }
}
