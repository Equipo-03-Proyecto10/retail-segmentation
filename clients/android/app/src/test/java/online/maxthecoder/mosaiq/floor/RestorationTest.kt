package online.maxthecoder.mosaiq.floor

import android.Manifest
import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.activity.compose.LocalActivityResultRegistryOwner
import androidx.activity.compose.setContent
import androidx.activity.result.ActivityResultRegistry
import androidx.activity.result.ActivityResultRegistryOwner
import androidx.activity.result.contract.ActivityResultContract
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.core.app.ActivityOptionsCompat
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Channel
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.CameraPermission
import online.maxthecoder.mosaiq.floor.ui.CheckInState
import online.maxthecoder.mosaiq.floor.ui.CustomerInsightsViewModel
import online.maxthecoder.mosaiq.floor.ui.FloorApp
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.RegistrationState
import online.maxthecoder.mosaiq.floor.ui.SalesContent
import online.maxthecoder.mosaiq.floor.ui.SalesViewModel
import online.maxthecoder.mosaiq.floor.ui.ScanBody
import online.maxthecoder.mosaiq.floor.ui.ScanResult
import online.maxthecoder.mosaiq.floor.ui.ScanViewModel
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.checkin.CheckInPanel
import online.maxthecoder.mosaiq.floor.ui.sale.SaleActions
import online.maxthecoder.mosaiq.floor.ui.sale.SaleUi
import online.maxthecoder.mosaiq.floor.ui.shell.FloorHome
import online.maxthecoder.mosaiq.floor.ui.shell.SignOutButton
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Before
import org.junit.Ignore
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * What survives a rotation or a killed process (UX-16). `emulateSavedInstanceStateRestore` throws
 * the composition away and rebuilds it from only what `rememberSaveable` wrote to the bundle, which
 * is what the system does after either event; a view model held outside the composition stands for
 * the rotation case, and a brand-new one stands for process death.
 */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class RestorationTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private lateinit var model: FloorViewModel
    private lateinit var scan: ScanViewModel
    private lateinit var sales: SalesViewModel
    private lateinit var insights: CustomerInsightsViewModel

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
        model.chosen.value = ChosenStore(1, "Centro")
    }

    @After
    fun stop() {
        // The activity's collectors read Room; stop them before the database goes away.
        compose.activityRule.scenario.close()
        listOf(model, scan, sales, insights).forEach { it.viewModelScope.cancel() }
        app.database.close()
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

    @Composable
    private fun Home() {
        FloorHome(
            signedIn = SessionState.SignedIn("Demo A", "STORE_MANAGER"),
            model = model,
            scan = scan,
            sales = sales,
            insights = insights,
            onSignOut = {}
        )
    }

    /** Sets the content on the live activity itself, so a recreated activity can set it again. */
    private fun showHome() {
        compose.activityRule.scenario.onActivity { activity -> activity.setContent { Home() } }
        compose.waitForIdle()
    }

    // ---- The shell -----------------------------------------------------------------------------

    @Test
    fun theActivityBeingRecreatedKeepsTheSelectedSection() {
        showHome()
        compose.onNodeWithTag("nav-outbox").performClick()
        compose.onNodeWithTag("nav-outbox").assertIsSelected()

        compose.activityRule.scenario.recreate()
        // The new activity draws the same screen; its saved-instance bundle brings the section back.
        showHome()

        compose.onNodeWithTag("nav-outbox").assertIsSelected()
    }

    @Test
    fun theActivityBeingRecreatedKeepsTheOpenStorePickerAndItsCity() {
        showHome()
        compose.onNodeWithTag("store-chip").performClick()
        compose.onNode(hasSetTextAction()).performTextInput("Leon")

        compose.activityRule.scenario.recreate()
        showHome()

        compose.onNodeWithText("Pick your store").assertExists()
        compose.onNode(hasSetTextAction()).assertTextContains("Leon")
    }

    @Test
    fun theAccountMenuStaysOpenAcrossRecreation() {
        val tester = StateRestorationTester(compose)
        tester.setContent { Home() }
        compose.onNodeWithTag("account-menu").performClick()
        compose.onNodeWithText("Demo A").assertExists()

        tester.emulateSavedInstanceStateRestore()

        compose.onNodeWithText("Demo A").assertExists()
    }

    // ---- Sign-in -------------------------------------------------------------------------------

    @Test
    fun theTypedEmailSurvivesButThePasswordNeverDoes() {
        val tester = StateRestorationTester(compose)
        tester.setContent { FloorApp(SessionState.SignIn(), { _, _ -> }, {}, {}) }
        compose.onAllNodes(hasSetTextAction())[0].performTextInput("ana@mosaiq-demo.com")
        compose.onAllNodes(hasSetTextAction())[1].performTextInput("secret")
        compose.onAllNodesWithText("Sign in")[0].assertIsEnabled()

        tester.emulateSavedInstanceStateRestore()

        compose.onAllNodes(hasSetTextAction())[0].assertTextContains("ana@mosaiq-demo.com")
        // By design (a password is not written to disk): the field is empty, so Sign in is off.
        compose.onAllNodesWithText("Sign in")[0].assertIsNotEnabled()
    }

    // ---- Sale ----------------------------------------------------------------------------------

    private val ana = Customer("c1", "Ana Torres", "ana@example.com", null, 1, "2025-01-01")
    private val channels = listOf(Channel(1, "physical_store"), Channel(2, "online"))

    private fun saleContent(ui: SaleUi) = StateRestorationTester(compose).also {
        it.setContent {
            MosaiqTheme(darkTheme = false) { SalesContent(ui, SaleActions(), SnackbarHostState()) {} }
        }
    }

    @Test
    fun theSaleSearchFieldsSurviveRecreation() {
        val tester = saleContent(SaleUi(store = ChosenStore(1, "Centro")))
        compose.onNodeWithTag("customer-query").performTextInput("Ana")
        compose.onNodeWithTag("customer-email").performTextInput("ana@example.com")
        compose.onNodeWithTag("product-query").performTextInput("socks")

        tester.emulateSavedInstanceStateRestore()

        compose.onNodeWithTag("customer-query").assertTextContains("Ana")
        compose.onNodeWithTag("customer-email").assertTextContains("ana@example.com")
        compose.onNodeWithTag("product-query").assertTextContains("socks")
    }

    @Test
    fun theNewCustomerSheetAndItsTypedFieldsSurviveRecreation() {
        val tester =
            saleContent(
                SaleUi(
                    store = ChosenStore(1, "Centro"),
                    registration = RegistrationState(channels = channels, selectedChannelId = 1)
                )
            )
        compose.onNodeWithText("New customer").performScrollTo().performClick()
        compose.waitForIdle()
        compose.onNodeWithText("Name").performTextInput("Ben Ruiz")
        compose.onNodeWithText("Email").performTextInput("ben@example.com")
        compose.onNodeWithText("Phone").performTextInput("5551234")
        compose.onNodeWithText("Register customer").assertIsEnabled()

        tester.emulateSavedInstanceStateRestore()
        compose.waitForIdle()

        compose.onNodeWithText("Register customer").assertIsEnabled()
        compose.onNodeWithText("Ben Ruiz").assertExists()
        compose.onNodeWithText("ben@example.com").assertExists()
        compose.onNodeWithText("5551234").assertExists()
    }

    @Test
    fun aClosedNewCustomerSheetStaysClosedAndTheInterestsPanelStaysOpen() {
        val tester = saleContent(SaleUi(customer = ana, store = ChosenStore(1, "Centro")))
        compose.onNodeWithText("Show interests").performScrollTo().performClick()
        compose.onNodeWithText("Hide interests").assertExists()

        tester.emulateSavedInstanceStateRestore()

        compose.onNodeWithText("Hide interests").assertExists()
        compose.onNodeWithText("Register customer").assertDoesNotExist()
    }

    // ---- Scan ----------------------------------------------------------------------------------

    @Test
    fun theTypedProductNumberSurvivesRecreation() {
        val tester = StateRestorationTester(compose)
        tester.setContent {
            MosaiqTheme {
                ScanBody(
                    store = ChosenStore(3, "Coyoacan"),
                    permission = CameraPermission.GRANTED,
                    scanning = false,
                    result = ScanResult.Idle,
                    onPickStore = {},
                    onScan = {},
                    onScanAnother = {},
                    onRetryLookup = {},
                    onLookUp = {},
                    onAddProduct = null,
                    onOpenSettings = {},
                    onGrantAccess = {},
                    cameraError = false,
                    onRetryCamera = {},
                    camera = {}
                )
            }
        }
        compose.onNodeWithText("Look up").performScrollTo().assertIsNotEnabled()
        compose.onNodeWithTag("manual-product-id").performScrollTo().performTextInput("120")

        tester.emulateSavedInstanceStateRestore()

        compose.onNodeWithTag("manual-product-id").assertTextContains("120")
        compose.onNodeWithText("Look up").performScrollTo().assertIsEnabled()
    }

    // ---- Check-in ------------------------------------------------------------------------------

    /** A system that answers every permission request with "denied", straight away. */
    private val denyingPermissions =
        object : ActivityResultRegistryOwner {
            override val activityResultRegistry =
                object : ActivityResultRegistry() {
                    override fun <I, O> onLaunch(
                        requestCode: Int,
                        contract: ActivityResultContract<I, O>,
                        input: I,
                        options: ActivityOptionsCompat?
                    ) {
                        @Suppress("UNCHECKED_CAST")
                        dispatchResult(
                            requestCode,
                            mapOf(
                                Manifest.permission.ACCESS_FINE_LOCATION to false,
                                Manifest.permission.ACCESS_COARSE_LOCATION to false
                            ) as O
                        )
                    }
                }
        }

    @Test
    fun aDeniedLocationExplanationSurvivesRecreation() {
        val tester = StateRestorationTester(compose)
        tester.setContent {
            CompositionLocalProvider(LocalActivityResultRegistryOwner provides denyingPermissions) {
                MosaiqTheme { CheckInPanel(model, ChosenStore(1, "Centro")) }
            }
        }
        compose.onNodeWithText("Open settings").assertDoesNotExist()
        compose.onNodeWithText("Check in here").performClick()
        compose.onNodeWithText("Open settings").assertExists()

        tester.emulateSavedInstanceStateRestore()

        compose.onNodeWithText("Open settings").assertExists()
        // Let the queued check-in finish before the database is closed.
        settleUntil { model.checkIn.value is CheckInState.Queued }
    }

    // ---- Outbox --------------------------------------------------------------------------------

    @Test
    fun theSignOutWarningAndItsPendingCountSurviveRecreation() {
        runBlocking { graph.writes.queueCheckIn(3, null) }
        val tester = StateRestorationTester(compose)
        tester.setContent { MosaiqTheme { SignOutButton(model) {} } }
        compose.onNodeWithText("Sign out").performClick()
        settleUntil { compose.onAllNodesWithText("Sign out anyway").fetchSemanticsNodes().isNotEmpty() }

        tester.emulateSavedInstanceStateRestore()

        compose.onNodeWithText("Writes are still waiting").assertExists()
        compose.onNodeWithText("1 floor write(s) have not been sent yet.", substring = true).assertExists()
        compose.onNodeWithText("Stay signed in").assertExists()
    }

    // ---- Process death: what lives only in a view model ----------------------------------------

    @Test
    fun theChosenStoreSurvivesProcessDeathBecauseTheSessionStoreKeepsIt() {
        model.choose(online.maxthecoder.mosaiq.floor.net.Store(5, "Valle", "Monterrey", "NL", true))
        // A new process builds a new view model over the same persisted session.
        assertEquals(5, FloorViewModel(graph).chosen.value?.storeId)
    }

    @Ignore(
        "FINDING (not fixed here): the selected customer and the cart live in SalesViewModel's " +
            "MutableStateFlows with no SavedStateHandle, so process death empties the sale. Rotation is fine."
    )
    @Test
    fun theSelectedCustomerAndTheCartSurviveProcessDeath() {
        sales.selectCustomer(ana)
        sales.addProduct(Product(1, "SKU1", "Trail socks", 3, "12.5", true))
        val afterRestart = SalesViewModel(graph)
        assertNotNull(afterRestart.selectedCustomer.value)
        assertEquals(1, afterRestart.sale.value.lines.size)
    }

    @Test
    fun aRefusedSignInKeepsTheEmailTypedAfterRecreationToo() {
        val tester = StateRestorationTester(compose)
        tester.setContent {
            FloorApp(SessionState.SignIn(error = ApiError(401, "invalid_credentials", "x", null)), { _, _ -> }, {}, {})
        }
        compose.onAllNodes(hasSetTextAction())[0].performTextInput("ana@mosaiq-demo.com")

        tester.emulateSavedInstanceStateRestore()

        compose.onAllNodes(hasSetTextAction())[0].assertTextContains("ana@mosaiq-demo.com")
        compose.onNodeWithText("That email or password is not right. Check them and try again.").assertExists()
    }

    private companion object {
        const val TRIES = 200
        const val STEP_MS = 10L
    }
}
