package online.maxthecoder.mosaiq.floor

import android.graphics.Bitmap
import android.graphics.Canvas
import androidx.activity.ComponentActivity
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.io.File
import java.time.Instant
import java.time.ZoneId
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Channel
import online.maxthecoder.mosaiq.floor.net.CurrentAssignment
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.RecommendationItem
import online.maxthecoder.mosaiq.floor.net.RecommendationResult
import online.maxthecoder.mosaiq.floor.net.Store
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.CameraPermission
import online.maxthecoder.mosaiq.floor.ui.CustomerInsightsContent
import online.maxthecoder.mosaiq.floor.ui.InsightsState
import online.maxthecoder.mosaiq.floor.ui.RegistrationState
import online.maxthecoder.mosaiq.floor.ui.SaleBuilderState
import online.maxthecoder.mosaiq.floor.ui.SaleLine
import online.maxthecoder.mosaiq.floor.ui.SalesContent
import online.maxthecoder.mosaiq.floor.ui.ScanBody
import online.maxthecoder.mosaiq.floor.ui.ScanResult
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.ShownRecommendation
import online.maxthecoder.mosaiq.floor.ui.SignInContent
import online.maxthecoder.mosaiq.floor.ui.StoresState
import online.maxthecoder.mosaiq.floor.ui.checkin.CheckInContent
import online.maxthecoder.mosaiq.floor.ui.checkin.CheckInStatus
import online.maxthecoder.mosaiq.floor.ui.checkin.StorePickerContent
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxContent
import online.maxthecoder.mosaiq.floor.ui.sale.RegistrationFields
import online.maxthecoder.mosaiq.floor.ui.sale.RegistrationForm
import online.maxthecoder.mosaiq.floor.ui.sale.SaleActions
import online.maxthecoder.mosaiq.floor.ui.sale.SaleUi
import online.maxthecoder.mosaiq.floor.ui.shell.FloorBottomBar
import online.maxthecoder.mosaiq.floor.ui.shell.FloorLocation
import online.maxthecoder.mosaiq.floor.ui.shell.FloorTopBar
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Renders the redesigned screens to PNG on the JVM, light and dark, with no device or emulator (UX-17).
 * Robolectric's native graphics draws the real Compose output; the files land in
 * `clients/android/app/build/screenshots/<name>-<light|dark>.png` (`-x2font` for 2.0 font scale).
 *
 * It is skipped unless `SCREENSHOTS=1`, so the normal `testDebugUnitTest` gate does not pay for it.
 * One command, from `clients/android`:
 *
 *     SCREENSHOTS=1 ./gradlew testDebugUnitTest --tests '*ScreenshotsTest'
 */
@RunWith(AndroidJUnit4::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], application = FloorTestApp::class, qualifiers = "w360dp-h800dp-xxhdpi")
class ScreenshotsTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    @Before
    fun onlyWhenAsked() = assumeTrue("set SCREENSHOTS=1 to render", System.getenv("SCREENSHOTS") == "1")

    private val out = File(System.getProperty("user.dir"), "build/screenshots").apply { mkdirs() }

    /**
     * Draws the activity's decor view into a bitmap. Compose's own captureToImage waits for a frame the
     * paused Robolectric looper never delivers, so the view tree is drawn directly.
     */
    private fun draw(): Bitmap {
        var bitmap: Bitmap? = null
        compose.runOnUiThread {
            val view = compose.activity.window.decorView
            bitmap = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
            view.draw(Canvas(bitmap!!))
        }
        return bitmap!!
    }

    /** Shows [content] in the light theme, captures it, flips to dark, captures again. */
    private fun shoot(name: String, fontScale: Float = 1f, content: @Composable () -> Unit) {
        var dark by mutableStateOf(false)
        compose.setContent {
            val base = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(base.density, fontScale)) {
                MosaiqTheme(darkTheme = dark) {
                    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                        Box(Modifier.fillMaxSize().padding(16.dp)) { content() }
                    }
                }
            }
        }
        val suffix = if (fontScale == 1f) "" else "-x2font"
        for (isDark in listOf(false, true)) {
            compose.runOnUiThread { dark = isDark }
            compose.waitForIdle()
            val bitmap = draw()
            File(out, "$name$suffix-${if (isDark) "dark" else "light"}.png").outputStream().use {
                bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
            }
        }
    }

    private val noop: () -> Unit = {}

    // Sign-in -------------------------------------------------------------------------------------

    private fun signIn(error: ApiError?, scale: Float = 1f, name: String) = shoot(name, scale) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            androidx.compose.foundation.layout.Column(
                verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(24.dp)
            ) {
                SignInContent("ana@mosaiq-demo.com", "secret", {}, {}, null, error, false, noop)
            }
        }
    }

    @Test fun signInEmpty() = shoot("signin-empty") {
        androidx.compose.foundation.layout.Column(
            verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(24.dp)
        ) { SignInContent("", "", {}, {}, null, null, false, noop) }
    }

    @Test fun signInError() = signIn(ApiError(401, "invalid_credentials", "raw", "corr-1"), name = "signin-error")

    @Test fun signInLargeFont() = signIn(ApiError(401, "invalid_credentials", "raw", "corr-1"), 2f, "signin-error")

    // Check-in ------------------------------------------------------------------------------------

    private fun checkIn(name: String, status: CheckInStatus, denied: Boolean = false) = shoot(name) {
        CheckInContent(
            "Centro",
            "Monterrey, NL",
            status,
            null,
            if (denied) "Location is off for this app." else null,
            denied,
            noop,
            noop,
            noop
        )
    }

    @Test fun checkInNotCheckedIn() = checkIn("checkin-not-checked-in", CheckInStatus.NotCheckedIn)

    @Test fun checkInDone() = checkIn("checkin-done", CheckInStatus.Done("14:32"))

    @Test fun checkInNeedsAttention() = checkIn(
        "checkin-needs-attention",
        CheckInStatus.NeedsAttention(ApiError(422, "too_far", "Too far away.", "c-9"))
    )

    @Test fun checkInLocationDenied() = checkIn("checkin-location-denied", CheckInStatus.Waiting, denied = true)

    // Store picker --------------------------------------------------------------------------------

    private val stores = listOf(
        Store(1, "Centro", "Monterrey", "NL", true),
        Store(2, "Valle", "Monterrey", "NL", true),
        Store(3, "Coyoacan", "Mexico City", "CDMX", true)
    )

    private fun picker(name: String, state: StoresState) = shoot(name) {
        StorePickerContent(state, state.city, {}, noop, noop, {}, {})
    }

    @Test fun storePickerResults() = picker("picker-results", StoresState(stores = stores, totalPages = 2))

    @Test fun storePickerEmpty() = picker("picker-empty", StoresState(city = "Atlantis"))

    @Test fun storePickerError() = picker("picker-error", StoresState(error = ApiError.unreachable(null)))

    // Scan ----------------------------------------------------------------------------------------

    private val shirt = Product(120, "SKU-120", "Cotton shirt", 4, "12.5", true)
    private val coyoacan = ChosenStore(3, "Coyoacan")

    private fun scan(
        name: String,
        result: ScanResult = ScanResult.Idle,
        permission: CameraPermission = CameraPermission.GRANTED,
        scanning: Boolean = false,
        store: ChosenStore? = coyoacan
    ) = shoot(name) {
        ScanBody(
            store, permission, scanning, result, noop, noop, noop, noop, {}, {}, noop, noop, false, noop
        ) {
            Box(Modifier.fillMaxSize().background(androidx.compose.ui.graphics.Color.DarkGray), Alignment.Center) {
                Text("camera preview", color = androidx.compose.ui.graphics.Color.White)
            }
        }
    }

    @Test fun scanReady() = scan("scan-ready", scanning = true)

    @Test fun scanIdle() = scan("scan-idle")

    @Test fun scanResult() = scan("scan-result", ScanResult.Shown(shirt, "Coyoacan", 14, null, 1L))

    @Test fun scanPermissionDenied() = scan("scan-permission-denied", permission = CameraPermission.DENIED)

    @Test fun scanNoStore() = scan("scan-no-store", store = null)

    // Sale ----------------------------------------------------------------------------------------

    private val store = ChosenStore(1, "Centro")
    private val ana = Customer("c1", "Ana Torres", "ana@example.com", null, 1, "2025-01-01")
    private val socks = Product(1, "SKU1", "Trail socks", 3, "12.5", true)
    private val channels = listOf(Channel(1, "physical_store"), Channel(2, "online"))
    private val ready = RegistrationState(channels = channels, selectedChannelId = 1)

    private fun sale(name: String, ui: SaleUi, scale: Float = 1f) = shoot(name, scale) {
        SalesContent(ui, SaleActions(), SnackbarHostState()) {
            CustomerInsightsContent(insightsState(), {}, {})
        }
    }

    private val full = SaleUi(
        customer = ana,
        store = store,
        registration = ready,
        segmentLabel = "Loyal regulars",
        sale = SaleBuilderState(lines = listOf(SaleLine(socks, 2), SaleLine(shirt, 1)))
    )

    @Test fun saleEmpty() = sale("sale-empty", SaleUi(store = store, registration = ready))

    @Test fun saleFull() = sale("sale-full", full)

    @Test fun saleLargeFont() = sale("sale-full", full, 2f)

    @Test fun saleRegistrationForm() = shoot("sale-registration") {
        RegistrationForm(RegistrationFields("Ben Ruiz", "ben@example.com", ""), {}, ready, {}, {}, noop)
    }

    // Customer insights ---------------------------------------------------------------------------

    private val assigned = CurrentAssignment("c1", "assigned", "loyal_regulars", 4, 3, 5, "2026-03-12T09:00:00Z")

    private fun insightsState() = InsightsState(
        customerId = "c1",
        assignment = assigned,
        items = listOf(
            ShownRecommendation(
                1,
                RecommendationItem(9, 2, 6, emptyList()),
                "Trail socks",
                listOf("3 other customers in the same segment bought it")
            )
        )
    )

    @Test fun insightsFull() = shoot("insights-full") { CustomerInsightsContent(insightsState(), {}, {}) }

    @Test fun insightsEmpty() = shoot("insights-empty") {
        CustomerInsightsContent(
            InsightsState(
                customerId = "c1",
                assignment = assigned,
                recommendations = RecommendationResult("c1", "none_match")
            ),
            {},
            {}
        )
    }

    // Outbox --------------------------------------------------------------------------------------

    private val now = Instant.parse("2026-10-26T20:00:00Z")

    private fun row(id: Long, op: Operation, state: RowState, body: String = "{}", attempts: Int = 0) = OutboxRow(
        id = id,
        operation = op,
        path = "/p",
        body = body,
        clientId = "client-$id",
        userId = "A",
        createdAt = now.toEpochMilli() - 3_600_000,
        state = state,
        attempts = attempts,
        code = if (state == RowState.REFUSED) "validation_failed" else null,
        message = if (state == RowState.REFUSED) "The sale has no items." else null,
        correlationId = if (state == RowState.REFUSED) "corr-77" else null
    )

    @Test fun outboxMixed() = shoot("outbox-mixed") {
        OutboxContent(
            listOf(
                row(1, Operation.CHECK_IN, RowState.PENDING, """{"store_id":3}""", 1),
                row(2, Operation.SALE, RowState.PENDING, attempts = 2),
                row(3, Operation.SALE, RowState.DONE),
                row(4, Operation.SALE, RowState.REFUSED)
            ),
            "A",
            mapOf(3 to "Centro"),
            {},
            now,
            ZoneId.of("UTC")
        )
    }

    @Test fun outboxEmpty() = shoot("outbox-empty") {
        OutboxContent(emptyList(), "A", emptyMap(), {}, now, ZoneId.of("UTC"))
    }

    // Shell ---------------------------------------------------------------------------------------

    @Test fun shell() = shoot("shell") {
        Scaffold(
            topBar = {
                FloorTopBar(SessionState.SignedIn("Ana Torres", "STORE_MANAGER"), store, noop, noop)
            },
            bottomBar = { FloorBottomBar(FloorLocation.Scan, 3) {} }
        ) { padding ->
            Box(Modifier.fillMaxSize().padding(padding), Alignment.Center) { Text("Section content") }
        }
    }
}
