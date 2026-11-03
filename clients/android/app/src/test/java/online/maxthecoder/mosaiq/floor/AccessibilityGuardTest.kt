package online.maxthecoder.mosaiq.floor

import android.content.Context
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.unit.Density
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.time.Instant
import java.time.ZoneId
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Channel
import online.maxthecoder.mosaiq.floor.net.CurrentAssignment
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.RecommendationItem
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
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/**
 * Walks the merged semantics tree of every major screen state (#731) and fails on a clickable
 * node without a name, or a button under the 44dp touch target. A new screen state is covered by
 * adding one `audit { ... }` test. This does not replace TalkBack or Accessibility Scanner on a phone.
 */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class, qualifiers = "w360dp-h800dp-xxhdpi")
class AccessibilityGuardTest {
    @get:Rule val compose = createComposeRule()

    private val noop: () -> Unit = {}

    /** Renders [content] at [fontScale] and returns every problem the walk found (empty is clean). */
    private fun audit(fontScale: Float = 1f, content: @Composable () -> Unit) {
        compose.setContent {
            val base = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(base.density, fontScale)) {
                MosaiqTheme {
                    Surface { content() }
                }
            }
        }
        compose.waitForIdle()
        val density = compose.density.density
        val problems = mutableListOf<String>()
        val clickable = compose.onAllNodes(hasClickAction()).fetchSemanticsNodes()
        assertTrue("no clickable node rendered: the state under audit is empty", clickable.isNotEmpty())
        clickable.forEach { node ->
            val name = describe(node)
            if (name.isBlank()) problems += "clickable node ${node.id} has no label"
            if (node.config.getOrNull(SemanticsProperties.Role) == Role.Button) {
                // The larger of the node and its touch bounds: a chip padded out by the 48dp minimum
                // counts, and a node scrolled out of view (its clipped bounds are empty) keeps its size.
                val touch = node.touchBoundsInRoot
                val w = maxOf(node.size.width.toFloat(), touch.width) / density
                val h = maxOf(node.size.height.toFloat(), touch.height) / density
                if (h < MIN_TARGET_DP || w < MIN_TARGET_DP) {
                    problems += "button '$name' is ${w}x${h}dp, under ${MIN_TARGET_DP}dp"
                }
            }
        }
        assertTrue(problems.joinToString("\n"), problems.isEmpty())
    }

    /** The node's own text or description, else any text merged in from below it. */
    private fun describe(node: SemanticsNode): String {
        val own =
            node.config.getOrNull(SemanticsProperties.ContentDescription)?.joinToString(" ").orEmpty() +
                node.config.getOrNull(SemanticsProperties.Text)?.joinToString(" ") { it.text }.orEmpty() +
                node.config.getOrNull(SemanticsProperties.EditableText)?.text.orEmpty()
        return if (own.isNotBlank()) own else node.children.joinToString(" ") { describe(it) }
    }

    // Sign-in and session ------------------------------------------------------------------------

    @Test fun signInEmpty() = audit { SignInContent("", "", {}, {}, null, null, false, noop) }

    @Test fun signInError() = audit {
        SignInContent(
            "ana@mosaiq-demo.com",
            "secret",
            {},
            {},
            "Signed out.",
            ApiError(401, "x", "raw", "c"),
            false,
            noop
        )
    }

    @Test fun signInBusy() = audit { SignInContent("ana@mosaiq-demo.com", "secret", {}, {}, null, null, true, noop) }

    @Test fun signInLargeFont() = audit(2f) {
        SignInContent("ana@mosaiq-demo.com", "secret", {}, {}, null, ApiError(401, "x", "raw", "c"), false, noop)
    }

    // Check-in -----------------------------------------------------------------------------------

    private fun checkIn(status: CheckInStatus, denied: Boolean = false, scale: Float = 1f) = audit(scale) {
        CheckInContent("Centro", "Monterrey, NL", status, null, "Note", denied, noop, noop, noop)
    }

    @Test fun checkInNotCheckedIn() = checkIn(CheckInStatus.NotCheckedIn)

    @Test fun checkInSending() = checkIn(CheckInStatus.Sending)

    @Test fun checkInWaitingDenied() = checkIn(CheckInStatus.Waiting, denied = true)

    @Test fun checkInDone() = checkIn(CheckInStatus.Done("14:32"))

    @Test fun checkInNeedsAttention() =
        checkIn(CheckInStatus.NeedsAttention(ApiError(422, "too_far", "Too far away.", "c-9")))

    @Test fun checkInLargeFont() = checkIn(CheckInStatus.Done("14:32"), denied = true, scale = 2f)

    // Store picker -------------------------------------------------------------------------------

    private val stores = listOf(Store(1, "Centro", "Monterrey", "NL", true), Store(2, "Valle", "Monterrey", "NL", true))

    private fun picker(state: StoresState, scale: Float = 1f) = audit(scale) {
        StorePickerContent(state, state.city, {}, noop, noop, {}, {})
    }

    @Test fun pickerResults() = picker(StoresState(stores = stores, totalPages = 2))

    @Test fun pickerEmpty() = picker(StoresState(city = "Atlantis"))

    @Test fun pickerError() = picker(StoresState(error = ApiError.unreachable(null)))

    @Test fun pickerLoading() = picker(StoresState(loading = true))

    @Test fun pickerLargeFont() = picker(StoresState(stores = stores, totalPages = 2), 2f)

    // Scan ---------------------------------------------------------------------------------------

    private val shirt = Product(120, "SKU-120", "Cotton shirt", 4, "12.5", true)
    private val coyoacan = ChosenStore(3, "Coyoacan")

    private fun scan(
        result: ScanResult = ScanResult.Idle,
        permission: CameraPermission = CameraPermission.GRANTED,
        scanning: Boolean = false,
        store: ChosenStore? = coyoacan,
        scale: Float = 1f
    ) = audit(scale) {
        ScanBody(store, permission, scanning, result, noop, noop, noop, noop, {}, {}, noop, noop, false, noop) {
            Text("camera")
        }
    }

    @Test fun scanIdle() = scan()

    @Test fun scanCamera() {
        scan(scanning = true)
        val description = ApplicationProvider.getApplicationContext<Context>().getString(
            R.string.scan_camera_description
        )
        compose.onNode(hasContentDescription(description)).assertExists()
    }

    @Test fun scanDenied() = scan(permission = CameraPermission.DENIED)

    @Test fun scanNoStore() = scan(store = null)

    @Test fun scanProduct() = scan(ScanResult.Shown(shirt, "Coyoacan", 14, null, 1L))

    @Test fun scanNotFound() = scan(ScanResult.ProductNotFound)

    @Test fun scanFailed() = scan(ScanResult.Failed(ApiError.unreachable(null)))

    @Test fun scanLargeFont() = scan(ScanResult.Shown(shirt, "Coyoacan", 14, null, 1L), scale = 2f)

    // Sale ---------------------------------------------------------------------------------------

    private val ana = Customer("c1", "Ana Torres", "ana@example.com", null, 1, "2025-01-01")
    private val socks = Product(1, "SKU1", "Trail socks", 3, "12.5", true)
    private val ready =
        RegistrationState(channels = listOf(Channel(1, "physical_store"), Channel(2, "online")), selectedChannelId = 1)
    private val full = SaleUi(
        customer = ana,
        store = ChosenStore(1, "Centro"),
        registration = ready,
        sale = SaleBuilderState(lines = listOf(SaleLine(socks, 2), SaleLine(shirt, 1)), products = listOf(socks))
    )

    private fun sale(ui: SaleUi, scale: Float = 1f) = audit(scale) {
        SalesContent(ui, SaleActions(), SnackbarHostState()) { CustomerInsightsContent(insights(), {}, {}) }
    }

    @Test fun saleEmpty() = sale(SaleUi(store = ChosenStore(1, "Centro"), registration = ready))

    @Test fun saleFull() = sale(full)

    @Test fun saleLargeFont() = sale(full, 2f)

    @Test fun saleRegistration() = audit {
        RegistrationForm(RegistrationFields("Ben Ruiz", "not-an-email", ""), {}, ready, {}, {}, noop)
    }

    // Insights -----------------------------------------------------------------------------------

    private fun insights() = InsightsState(
        customerId = "c1",
        assignment = CurrentAssignment("c1", "assigned", "loyal_regulars", 4, 3, 5, "2026-03-12T09:00:00Z"),
        items = listOf(
            ShownRecommendation(1, RecommendationItem(9, 2, 6, emptyList()), "Trail socks", listOf("Bought together"))
        )
    )

    @Test fun insightsFull() = audit { CustomerInsightsContent(insights(), {}, {}) }

    // Outbox -------------------------------------------------------------------------------------

    private val now = Instant.parse("2026-10-26T20:00:00Z")

    private fun row(id: Long, op: Operation, state: RowState) = OutboxRow(
        id = id,
        operation = op,
        path = "/p",
        body = "{}",
        clientId = "client-$id",
        userId = "A",
        createdAt = now.toEpochMilli() - 3_600_000,
        state = state,
        attempts = 1,
        code = if (state == RowState.REFUSED) "validation_failed" else null,
        message = if (state == RowState.REFUSED) "The sale has no items." else null,
        correlationId = null
    )

    @Test fun outboxMixed() = audit {
        OutboxContent(
            listOf(
                row(1, Operation.CHECK_IN, RowState.PENDING),
                row(2, Operation.SALE, RowState.DONE),
                row(3, Operation.SALE, RowState.REFUSED)
            ),
            "A",
            emptyMap(),
            {},
            now,
            ZoneId.of("UTC")
        )
    }

    @Test fun outboxLargeFont() = audit(2f) {
        OutboxContent(listOf(row(3, Operation.SALE, RowState.REFUSED)), "A", emptyMap(), {}, now, ZoneId.of("UTC"))
    }

    // Shell --------------------------------------------------------------------------------------

    @Test fun shellBars() = audit {
        Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            topBar = {
                FloorTopBar(
                    SessionState.SignedIn("Ana Torres", "STORE_MANAGER"),
                    ChosenStore(1, "Centro"),
                    noop,
                    noop
                )
            },
            bottomBar = { FloorBottomBar(FloorLocation.Scan, 3) {} }
        ) { padding -> Text("Section", Modifier.padding(padding)) }
    }

    @Test fun shellBarsLargeFont() {
        audit(2f) {
            Scaffold(
                topBar = { FloorTopBar(SessionState.SignedIn("Ana", "MANAGER"), ChosenStore(1, "Centro"), noop, noop) },
                bottomBar = { FloorBottomBar(FloorLocation.Scan, 3) {} }
            ) { padding -> Text("Section", Modifier.padding(padding)) }
        }
        // Each tab is named once, whether or not its label is drawn.
        listOf("nav-check-in" to "Check-in", "nav-scan" to "Scan", "nav-sale" to "Sale").forEach { (tag, name) ->
            val node = compose.onNodeWithTag(tag).fetchSemanticsNode()
            assertEquals(tag, listOf(name), spoken(node))
        }
    }

    /** Every text and description reached from [node], in order. */
    private fun spoken(node: SemanticsNode): List<String> = (
        node.config.getOrNull(SemanticsProperties.ContentDescription).orEmpty() +
            node.config.getOrNull(SemanticsProperties.Text).orEmpty().map { it.text }
        ).ifEmpty { node.children.flatMap { spoken(it) } }

    private companion object {
        const val MIN_TARGET_DP = 44f
    }
}
