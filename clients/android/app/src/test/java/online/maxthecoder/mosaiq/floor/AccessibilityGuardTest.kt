package online.maxthecoder.mosaiq.floor

import android.content.Context
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.text.TextLayoutResult
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
import org.robolectric.annotation.GraphicsMode

/**
 * Walks the semantics tree of every major screen state (#731, UX-13), once at the default font size
 * and once at 200%, and fails on any of:
 * - a node with a click action and no label (text, description or merged child text);
 * - a node with a click action and no role (text fields, announced as edit boxes, are exempt);
 * - a node with a click action whose touch target is under 44dp;
 * - a control clipped, wholly or in part, with no scrolling ancestor to bring it into view;
 * - a text whose layout overflows its bounds or is cut with an ellipsis.
 * A new screen state is covered by adding one `audit { ... }` test. This does not replace TalkBack or
 * Accessibility Scanner on a phone.
 */
@RunWith(AndroidJUnit4::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], application = FloorTestApp::class, qualifiers = "w360dp-h800dp-xxhdpi")
class AccessibilityGuardTest {
    @get:Rule val compose = createComposeRule()

    private val noop: () -> Unit = {}

    /**
     * Renders [content] at the default font size and then at 200%, runs [before] once (to open a
     * menu, say), and fails with every problem the walk found at either size.
     */
    private fun audit(before: () -> Unit = {}, content: @Composable () -> Unit) {
        var fontScale by mutableFloatStateOf(1f)
        compose.setContent {
            val base = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(base.density, fontScale)) {
                MosaiqTheme {
                    Surface { content() }
                }
            }
        }
        compose.waitForIdle()
        before()
        compose.waitForIdle()
        val problems = walk("1.0x")
        fontScale = LARGE_FONT
        compose.waitForIdle()
        problems += walk("2.0x")
        assertTrue(problems.joinToString("\n"), problems.isEmpty())
    }

    /** Every problem in the current tree, each prefixed with [scale]. */
    private fun walk(scale: String): MutableList<String> {
        val density = compose.density.density
        val problems = mutableListOf<String>()
        compose.onAllNodes(hasClickAction()).fetchSemanticsNodes().forEach { node ->
            val name = describe(node)
            if (name.isBlank()) problems += "$scale: clickable node ${node.id} has no label"
            val role = node.config.getOrNull(SemanticsProperties.Role)
            // A text field, even a locked one with no SetText, is announced as an edit box.
            if (role == null && SemanticsProperties.EditableText !in node.config) {
                problems += "$scale: clickable '$name' has no role"
            }
            // The larger of the node and its touch bounds: a chip padded out by the 48dp minimum
            // counts, and a node scrolled out of view (its clipped bounds are empty) keeps its size.
            val touch = node.touchBoundsInRoot
            val w = maxOf(node.size.width.toFloat(), touch.width) / density
            val h = maxOf(node.size.height.toFloat(), touch.height) / density
            if (h < MIN_TARGET_DP || w < MIN_TARGET_DP) {
                problems += "$scale: '$name' is ${w}x${h}dp, under ${MIN_TARGET_DP}dp"
            }
            // A control cut by its container is lost unless something scrolls it back into view.
            val shown = node.boundsInRoot
            val clipped =
                shown.width < node.size.width - CLIP_SLACK_PX || shown.height < node.size.height - CLIP_SLACK_PX
            if (clipped && !scrolls(node)) problems += "$scale: '$name' is clipped and cannot be scrolled to"
        }
        val texts = SemanticsMatcher.keyIsDefined(SemanticsActions.GetTextLayoutResult)
        compose.onAllNodes(texts, useUnmergedTree = true).fetchSemanticsNodes().forEach { node ->
            val layouts = mutableListOf<TextLayoutResult>()
            node.config[SemanticsActions.GetTextLayoutResult].action?.invoke(layouts)
            val layout = layouts.firstOrNull() ?: return@forEach
            // Height and ellipsis only: the semantics re-layout reports a short line's intrinsic width,
            // so its width overflow is noise; a wrapped text that is cut shows in its height.
            val cut = layout.didOverflowHeight || (0 until layout.lineCount).any(layout::isLineEllipsized)
            if (cut) problems += "$scale: text '${layout.layoutInput.text}' is cut off"
        }
        return problems
    }

    /** Whether [node] or an ancestor can scroll, so a part out of view can be brought back. */
    private fun scrolls(node: SemanticsNode): Boolean =
        generateSequence(node) { it.parent }.any { SemanticsActions.ScrollBy in it.config }

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

    // Check-in -----------------------------------------------------------------------------------

    private fun checkIn(status: CheckInStatus, denied: Boolean = false) = audit {
        CheckInContent("Centro", "Monterrey, NL", status, null, "Note", denied, noop, noop, noop)
    }

    @Test fun checkInNotCheckedIn() = checkIn(CheckInStatus.NotCheckedIn)

    @Test fun checkInSending() = checkIn(CheckInStatus.Sending)

    @Test fun checkInWaitingDenied() = checkIn(CheckInStatus.Waiting, denied = true)

    @Test fun checkInDone() = checkIn(CheckInStatus.Done("14:32"))

    @Test fun checkInDoneDenied() = checkIn(CheckInStatus.Done("14:32"), denied = true)

    @Test fun checkInNeedsAttention() =
        checkIn(CheckInStatus.NeedsAttention(ApiError(422, "too_far", "Too far away.", "c-9")))

    // Store picker -------------------------------------------------------------------------------

    private val stores = listOf(Store(1, "Centro", "Monterrey", "NL", true), Store(2, "Valle", "Monterrey", "NL", true))

    private fun picker(state: StoresState) = audit {
        StorePickerContent(state, state.city, {}, noop, noop, {}, {})
    }

    @Test fun pickerResults() = picker(StoresState(stores = stores, totalPages = 2))

    @Test fun pickerEmpty() = picker(StoresState(city = "Atlantis"))

    @Test fun pickerError() = picker(StoresState(error = ApiError.unreachable(null)))

    @Test fun pickerLoading() = picker(StoresState(loading = true))

    // Scan ---------------------------------------------------------------------------------------

    private val shirt = Product(120, "SKU-120", "Cotton shirt", 4, "12.5", true)
    private val coyoacan = ChosenStore(3, "Coyoacan")

    private fun scan(
        result: ScanResult = ScanResult.Idle,
        permission: CameraPermission = CameraPermission.GRANTED,
        scanning: Boolean = false,
        store: ChosenStore? = coyoacan
    ) = audit {
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

    private fun sale(ui: SaleUi) = audit {
        SalesContent(ui, SaleActions(), SnackbarHostState()) { CustomerInsightsContent(insights(), {}, {}) }
    }

    @Test fun saleEmpty() = sale(SaleUi(store = ChosenStore(1, "Centro"), registration = ready))

    @Test fun saleFull() = sale(full)

    @Test fun saleNoStore() = sale(SaleUi(registration = ready))

    @Test fun saleSearchError() = sale(
        SaleUi(
            store = ChosenStore(1, "Centro"),
            registration = ready,
            sale = SaleBuilderState(error = ApiError.unreachable(null))
        )
    )

    @Test fun saleRegistration() = audit {
        RegistrationForm(RegistrationFields("Ben Ruiz", "not-an-email", ""), {}, ready, {}, {}, noop)
    }

    // Insights -----------------------------------------------------------------------------------

    private fun insights() = InsightsState(
        customerId = "c1",
        assignment = CurrentAssignment("c1", "assigned", "loyal_regulars", 4, 3, 5, "2026-03-12T09:00:00Z"),
        items = listOf(
            ShownRecommendation(1, RecommendationItem(9, 2, 6, emptyList()), "Trail socks", listOf("Bought together"))
        ),
        clickNote = "That click was not recorded."
    )

    /** The card as the sale screen hosts it: inside its scrolling column. */
    private fun insightsCard(state: InsightsState) = audit {
        Column(Modifier.verticalScroll(rememberScrollState())) { CustomerInsightsContent(state, {}, {}) }
    }

    @Test fun insightsFull() = insightsCard(insights())

    @Test fun insightsLoading() = insightsCard(InsightsState(customerId = "c1", loading = true))

    @Test fun insightsError() = insightsCard(
        InsightsState(
            customerId = "c1",
            assignmentError = ApiError.unreachable(null),
            recommendationsError = ApiError.unreachable(null)
        )
    )

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
            ZoneId.of("UTC"),
            onSendNow = noop
        )
    }

    @Test fun outboxEmpty() = audit { OutboxContent(emptyList(), "A", emptyMap(), {}, now, ZoneId.of("UTC")) }

    // Shell --------------------------------------------------------------------------------------

    @Composable
    private fun Bars() {
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

    @Test fun shellBars() {
        audit { Bars() }
        // At 200% each tab is named once, whether or not its label is drawn.
        listOf("nav-check-in" to "Check-in", "nav-scan" to "Scan", "nav-sale" to "Sale").forEach { (tag, name) ->
            val node = compose.onNodeWithTag(tag).fetchSemanticsNode()
            assertEquals(tag, listOf(name), spoken(node))
        }
    }

    @Test fun shellAccountMenuOpen() = audit(before = { compose.onNodeWithTag("account-menu").performClick() }) {
        Bars()
    }

    /** Every text and description reached from [node], in order. */
    private fun spoken(node: SemanticsNode): List<String> = (
        node.config.getOrNull(SemanticsProperties.ContentDescription).orEmpty() +
            node.config.getOrNull(SemanticsProperties.Text).orEmpty().map { it.text }
        ).ifEmpty { node.children.flatMap { spoken(it) } }

    private companion object {
        const val MIN_TARGET_DP = 44f
        const val LARGE_FONT = 2f

        /** Rounding between a node's measured size and its laid-out bounds. */
        const val CLIP_SLACK_PX = 1f
    }
}
