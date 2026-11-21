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
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.unit.Density
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import online.maxthecoder.mosaiq.floor.alerts.AlertKind
import online.maxthecoder.mosaiq.floor.alerts.CampaignAlert
import online.maxthecoder.mosaiq.floor.alerts.Inbox
import online.maxthecoder.mosaiq.floor.auth.RecordPart
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Availability
import online.maxthecoder.mosaiq.floor.net.CaptureIncident
import online.maxthecoder.mosaiq.floor.net.Category
import online.maxthecoder.mosaiq.floor.net.Channel
import online.maxthecoder.mosaiq.floor.net.ConsentDecision
import online.maxthecoder.mosaiq.floor.net.ConsentState
import online.maxthecoder.mosaiq.floor.net.CurrentAssignment
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.DemandCoverageReport
import online.maxthecoder.mosaiq.floor.net.FloorCurrentAssignment
import online.maxthecoder.mosaiq.floor.net.FloorCustomer
import online.maxthecoder.mosaiq.floor.net.InferredInterest
import online.maxthecoder.mosaiq.floor.net.InterestProfile
import online.maxthecoder.mosaiq.floor.net.ItemAvailability
import online.maxthecoder.mosaiq.floor.net.KpiDefinitions
import online.maxthecoder.mosaiq.floor.net.KpiReport
import online.maxthecoder.mosaiq.floor.net.PreferenceChange
import online.maxthecoder.mosaiq.floor.net.PreferenceProfile
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.net.PromotionPage
import online.maxthecoder.mosaiq.floor.net.PurposeConsent
import online.maxthecoder.mosaiq.floor.net.RecommendationItem
import online.maxthecoder.mosaiq.floor.net.RecommendationItemV2
import online.maxthecoder.mosaiq.floor.net.RestrictionProfile
import online.maxthecoder.mosaiq.floor.net.Store
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.CONSENT_PURPOSES
import online.maxthecoder.mosaiq.floor.ui.CameraPermission
import online.maxthecoder.mosaiq.floor.ui.CardRead
import online.maxthecoder.mosaiq.floor.ui.CatalogSearch
import online.maxthecoder.mosaiq.floor.ui.CommercialProfile
import online.maxthecoder.mosaiq.floor.ui.CustomerInsightsContent
import online.maxthecoder.mosaiq.floor.ui.DiscountState
import online.maxthecoder.mosaiq.floor.ui.EnrolState
import online.maxthecoder.mosaiq.floor.ui.ExplainedItem
import online.maxthecoder.mosaiq.floor.ui.ExplainedState
import online.maxthecoder.mosaiq.floor.ui.FloorProfileState
import online.maxthecoder.mosaiq.floor.ui.FloorSearchState
import online.maxthecoder.mosaiq.floor.ui.GRANTED
import online.maxthecoder.mosaiq.floor.ui.HistoryState
import online.maxthecoder.mosaiq.floor.ui.InsightsState
import online.maxthecoder.mosaiq.floor.ui.PROGRAMME_DEFAULT
import online.maxthecoder.mosaiq.floor.ui.PartWrite
import online.maxthecoder.mosaiq.floor.ui.PromotionsContent
import online.maxthecoder.mosaiq.floor.ui.PromotionsState
import online.maxthecoder.mosaiq.floor.ui.RecordState
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
import online.maxthecoder.mosaiq.floor.ui.WITHDRAWN
import online.maxthecoder.mosaiq.floor.ui.checkin.CheckInContent
import online.maxthecoder.mosaiq.floor.ui.checkin.CheckInStatus
import online.maxthecoder.mosaiq.floor.ui.checkin.StorePickerContent
import online.maxthecoder.mosaiq.floor.ui.identify.IdentifyBody
import online.maxthecoder.mosaiq.floor.ui.identify.ProfileBody
import online.maxthecoder.mosaiq.floor.ui.inbox.InboxContent
import online.maxthecoder.mosaiq.floor.ui.inbox.focusOf
import online.maxthecoder.mosaiq.floor.ui.indicators.IndicatorPeriod
import online.maxthecoder.mosaiq.floor.ui.indicators.Indicators
import online.maxthecoder.mosaiq.floor.ui.indicators.IndicatorsContent
import online.maxthecoder.mosaiq.floor.ui.indicators.IndicatorsState
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxContent
import online.maxthecoder.mosaiq.floor.ui.record.EnrolFields
import online.maxthecoder.mosaiq.floor.ui.record.NewCustomerBody
import online.maxthecoder.mosaiq.floor.ui.record.RecordActions
import online.maxthecoder.mosaiq.floor.ui.record.RecordBody
import online.maxthecoder.mosaiq.floor.ui.sale.HistorySection
import online.maxthecoder.mosaiq.floor.ui.sale.RegistrationFields
import online.maxthecoder.mosaiq.floor.ui.sale.RegistrationForm
import online.maxthecoder.mosaiq.floor.ui.sale.ReviewContent
import online.maxthecoder.mosaiq.floor.ui.sale.SaleActions
import online.maxthecoder.mosaiq.floor.ui.sale.SaleBar
import online.maxthecoder.mosaiq.floor.ui.sale.SaleOutcome
import online.maxthecoder.mosaiq.floor.ui.sale.SaleUi
import online.maxthecoder.mosaiq.floor.ui.scan.CatalogSearchSection
import online.maxthecoder.mosaiq.floor.ui.scan.ScanAnswer
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

    // A refused sale in the history, with what the seller can do about it (F27-13).
    // Lazy: `row` reads `now`, which is declared with the outbox states below.
    private val refusedNeedsAttention by lazy { row(3, Operation.SALE, RowState.REFUSED) }

    private val refusedHistory by lazy {
        SaleUi(
            store = ChosenStore(1, "Centro"),
            registration = ready,
            history = listOf(row(4, Operation.SALE, RowState.PENDING), refusedNeedsAttention),
            kept = setOf("client-3")
        )
    }

    @Test fun saleRefusedRowCanBeCorrected() = sale(refusedHistory)

    @Test fun saleRefusedRowWhileAnotherSaleIsBuilt() = sale(refusedHistory.copy(sale = full.sale))

    @Test fun saleRefusedRowWhoseCartIsGone() = sale(refusedHistory.copy(kept = emptySet()))

    @Test fun saleRefusedRowOpenForCorrection() =
        sale(refusedHistory.copy(sale = full.sale.copy(corrects = listOf("client-3"))))

    @Test fun saleRefusedOnScreenCanBeDiscarded() = sale(
        full.copy(
            sale = full.sale.copy(queuedRowId = 3),
            queued = refusedNeedsAttention,
            history = listOf(refusedNeedsAttention),
            kept = setOf("client-3")
        )
    )

    @Test fun saleQueuedDraftIsPending() = sale(
        full.copy(
            sale = full.sale.copy(queuedRowId = 4),
            queued = row(4, Operation.SALE, RowState.PENDING),
            history = listOf(row(4, Operation.SALE, RowState.PENDING))
        )
    )

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

    @Test fun outboxLoading() = audit { OutboxContent(null, "A", emptyMap(), {}, now, ZoneId.of("UTC")) }

    // Identify (#856) ---------------------------------------------------------------------------

    private val jordan = FloorCustomer("7b3ad781-c5de-4f40-ae50-1fa825ce2b25", "Jordan Lee", "jordan@example.com")

    private fun identify(
        card: CardRead = CardRead.Idle,
        search: FloorSearchState = FloorSearchState(),
        permission: CameraPermission = CameraPermission.GRANTED,
        scanning: Boolean = false,
        cameraError: Boolean = false
    ) = audit {
        IdentifyBody(permission, scanning, cameraError, card, search, noop, noop, noop, { _, _ -> }, {}) {
            Text("camera")
        }
    }

    @Test fun identifyIdle() = identify()

    @Test fun identifyScanning() = identify(scanning = true)

    @Test fun identifyCameraError() = identify(scanning = true, cameraError = true)

    @Test fun identifyDenied() = identify(permission = CameraPermission.DENIED)

    @Test fun identifyLooking() = identify(CardRead.Looking)

    @Test fun identifyNotACard() = identify(CardRead.NotACard)

    @Test fun identifyUnknownCard() = identify(CardRead.Unknown)

    @Test fun identifyRevokedCard() = identify(CardRead.Revoked)

    @Test fun identifyCardFailed() = identify(CardRead.Failed(ApiError.unreachable(null)))

    @Test fun identifySearchResults() = identify(
        search = FloorSearchState(
            query = "Jordan",
            page = 2,
            totalPages = 3,
            customers = listOf(jordan, FloorCustomer("c2", "Jordan Smith", phone = "5551234")),
            searched = true
        )
    )

    @Test fun identifySearchEmpty() = identify(search = FloorSearchState(query = "Zed", searched = true))

    @Test fun identifySearchLoading() = identify(search = FloorSearchState(query = "Jordan", loading = true))

    @Test fun identifySearchError() = identify(
        search = FloorSearchState(query = "Jordan", error = ApiError.unreachable(null))
    )

    // Identify profile (#856, #860) --------------------------------------------------------------

    private val id = jordan.customerId

    private fun commercial(personalized: Boolean = true) = CommercialProfile(
        segment = FloorCurrentAssignment(id, "assigned", "LOYAL", "2026-10-12T03:00:00Z"),
        interests = InterestProfile(id, listOf(11), listOf(InferredInterest(12, 42))),
        restrictions = RestrictionProfile(id, personalized, listOf(5), listOf(9)),
        categoryNames = mapOf(9 to "Wine", 11 to "Cheese", 12 to "Yogurt"),
        channelNames = mapOf(5 to "call_center")
    )

    private fun explained(rank: Int, kind: String, name: String, vararg reasons: String) = ExplainedItem(
        RecommendationItemV2(
            productId = 100 + rank,
            categoryId = 11,
            kind = kind,
            position = rank,
            availability = ItemAvailability(3, 1, 4),
            reasons = emptyList()
        ),
        name,
        "4 in stock at Coyoacan",
        if (kind == "primary") null else "Stands in for another product",
        reasons.toList()
    )

    private val shelf = ExplainedState(
        customerId = id,
        items = listOf(
            explained(1, "primary", "Trail socks", "Bought together with your last visit"),
            explained(2, "alternative", "Wool socks", "Similar to Trail socks"),
            explained(3, "elsewhere", "Rain shell", "Held at Valle")
        )
    )

    private fun profile(
        state: FloorProfileState,
        recommendations: ExplainedState? = null,
        onTap: ((ExplainedItem) -> Unit)? = null
    ) = audit {
        ProfileBody(state, onRetry = {}, onBack = {}, recommendations = recommendations, onTap = onTap)
    }

    private val seen = FloorProfileState(id, jordan, byCard = true, commercial = commercial())

    @Test fun profileFull() = profile(seen)

    @Test fun profileObjected() = profile(seen.copy(commercial = commercial(personalized = false)))

    @Test fun profileLoading() = profile(FloorProfileState(id, loading = true))

    @Test fun profileError() = profile(FloorProfileState(id, error = ApiError.unreachable(null)))

    @Test fun profilePartsFailed() = profile(
        seen.copy(
            commercial = CommercialProfile(
                segmentError = ApiError.unreachable(null),
                interestsError = ApiError.unreachable(null),
                restrictionsError = ApiError.unreachable(null)
            )
        )
    )

    @Test fun profileUnassigned() = profile(
        seen.copy(
            commercial = commercial().copy(
                segment = FloorCurrentAssignment(id, "unassigned_result", null, "2026-10-12T03:00:00Z")
            )
        )
    )

    @Test fun profileRecommendations() = profile(seen, shelf, onTap = {})

    @Test fun profileRecommendationsReadOnly() = profile(seen, shelf)

    @Test fun profileRecommendationsLoading() = profile(seen, ExplainedState(customerId = id, loading = true))

    @Test fun profileRecommendationsNoStore() = profile(seen, ExplainedState(customerId = id, noStore = true))

    @Test fun profileRecommendationsError() = profile(
        seen,
        ExplainedState(customerId = id, error = ApiError.unreachable(null))
    )

    // Customer record and consent (#863) ---------------------------------------------------------

    private fun enrol(state: EnrolState, fields: EnrolFields = EnrolFields("Ana Torres", "ana@example.com")) = audit {
        NewCustomerBody(fields, {}, state, noop, {}, { _, _ -> }, noop)
    }

    private val physical = Channel(3, "physical_store")

    @Test fun enrolEmpty() = enrol(EnrolState(channels = listOf(physical, Channel(1, "online"))), EnrolFields())

    @Test fun enrolAnswered() = enrol(
        EnrolState(
            channels = listOf(physical),
            selectedChannelId = 3,
            consent = CONSENT_PURPOSES.associateWith { if (it == "PROFILING") WITHDRAWN else GRANTED }
        )
    )

    @Test fun enrolSending() = enrol(
        EnrolState(
            channels = listOf(physical),
            selectedChannelId = 3,
            consent = CONSENT_PURPOSES.associateWith { GRANTED },
            sending = true
        )
    )

    @Test fun enrolRefused() = enrol(
        EnrolState(channels = listOf(physical), error = ApiError(422, "invalid_body", "Check the fields.", "c-1"))
    )

    @Test fun enrolRegisteredNoConsentYet() = enrol(
        EnrolState(
            channels = listOf(physical),
            customer = ana,
            timeoutAdvice = true,
            error = ApiError.unreachable(null)
        )
    )

    private val consentSet = ConsentState(
        "c",
        "2026-11-05T14:30:00Z",
        CONSENT_PURPOSES.mapIndexed { i, purpose ->
            PurposeConsent(
                purpose,
                if (i == 0) GRANTED else "NOT_RECORDED",
                basis = if (i == 1) PROGRAMME_DEFAULT else null
            )
        }
    )

    private val record = RecordState(
        customerId = "c",
        customer = FloorCustomer("c", "Jordan Lee", "jordan@example.com", "5551234"),
        consent = consentSet,
        profile = PreferenceProfile("c", listOf(3), listOf(11)),
        channels = listOf(physical, Channel(1, "online")),
        categories = listOf(Category(11, "Cheese"), Category(12, "Wine")),
        consentDraft = CONSENT_PURPOSES.associateWith { GRANTED },
        channelsDraft = setOf(3, 1),
        interestsDraft = setOf(11, 12)
    )

    private fun recordBody(state: RecordState, history: HistoryState = HistoryState(), userId: String? = "A") = audit {
        RecordBody(state, history, userId, RecordActions())
    }

    @Test fun recordFull() = recordBody(record)

    @Test fun recordLoading() = recordBody(RecordState(customerId = "c", loading = true))

    @Test fun recordError() = recordBody(RecordState(customerId = "c", error = ApiError.unreachable(null)))

    @Test fun recordWrites() = recordBody(
        record.copy(
            writes = mapOf(
                RecordPart.CONSENT to PartWrite(saved = true),
                RecordPart.PREFERRED_CHANNELS to PartWrite(saving = true),
                RecordPart.INTERESTS to PartWrite(error = ApiError(403, "forbidden_role", "Not allowed.", "c-2"))
            )
        )
    )

    @Test fun recordHistory() = recordBody(
        record,
        HistoryState(
            open = true,
            consent = listOf(
                ConsentDecision(1, "c", "PROFILING", GRANTED, "2026-11-05T14:30:00Z", "A", "APP", "EXPLICIT"),
                ConsentDecision(
                    2,
                    "c",
                    "MARKETING_CONTACT",
                    WITHDRAWN,
                    "2026-11-01T10:00:00Z",
                    null,
                    "BACKFILL",
                    PROGRAMME_DEFAULT
                )
            ),
            changes = listOf(
                PreferenceChange(1, "c", "PREFERRED_CHANNELS", listOf(3), listOf(3, 1), "2026-11-05T14:31:00Z", "A"),
                PreferenceChange(2, "c", "INTERESTS", emptyList(), listOf(11), "2026-11-05T14:32:00Z", "B")
            )
        )
    )

    @Test fun recordHistoryLoading() = recordBody(record, HistoryState(open = true, loading = true))

    @Test fun recordHistoryEmpty() = recordBody(record, HistoryState(open = true))

    @Test fun recordHistoryError() = recordBody(record, HistoryState(open = true, error = ApiError.unreachable(null)))

    // Promotions (#861) --------------------------------------------------------------------------

    private fun offer(id: Int, name: String) =
        Promotion(id, 12, name, "15.00", "2026-11-01", "2026-11-30", listOf(3), listOf(3), true)

    private fun promotions(state: PromotionsState) = audit {
        Column(Modifier.verticalScroll(rememberScrollState())) { PromotionsContent(state, {}, {}) }
    }

    private val offers = PromotionsState(
        customerId = "c1",
        storeId = 3,
        promotions = listOf(offer(38, "Loyal customers"), offer(39, "Weekend basket")),
        recorded = setOf(39)
    )

    @Test fun promotionsListed() = promotions(offers)

    @Test fun promotionsNone() = promotions(PromotionsState(customerId = "c1", storeId = 3))

    @Test fun promotionsLoading() = promotions(PromotionsState(customerId = "c1", storeId = 3, loading = true))

    @Test fun promotionsError() = promotions(
        PromotionsState(customerId = "c1", storeId = 3, error = ApiError.unreachable(null))
    )

    @Test fun promotionsArmUnknown() = promotions(offers.copy(armError = ApiError.unreachable(null)))

    @Test fun promotionsNote() = promotions(
        PromotionsState(customerId = "c1", note = "Check in at a store to see its promotions.")
    )

    // Inbox (#869) -------------------------------------------------------------------------------

    private val incident = CaptureIncident(
        "i-1",
        "SALE",
        3,
        409,
        "stock_unavailable",
        "2026-11-05T14:02:10Z",
        "2026-11-05T14:02:41Z",
        "OPEN"
    )

    private val inbox = Inbox(
        campaigns = listOf(
            CampaignAlert(12, offer(38, "Loyal customers"), endingSoon = false),
            CampaignAlert(14, offer(41, "Weekend basket"), endingSoon = true)
        ),
        incidents = listOf(incident),
        readsIncidents = true,
        polledAt = Instant.EPOCH
    )

    private fun inbox(
        inbox: Inbox,
        notificationsOn: Boolean = true,
        refused: Int = 0,
        focus: String? = null,
        refreshing: Boolean = false
    ) = audit {
        InboxContent(
            inbox,
            setOf(AlertKind.CAMPAIGN_STARTED to "14"),
            refused,
            focus,
            refreshing,
            notificationsOn,
            noop,
            noop
        )
    }

    @Test fun inboxFull() = inbox(inbox, refused = 2)

    @Test fun inboxNotificationsOff() = inbox(inbox, notificationsOn = false)

    @Test fun inboxFocused() = inbox(inbox, focus = focusOf(AlertKind.CAMPAIGN_STARTED, "14"))

    @Test fun inboxRefreshing() = inbox(inbox, refreshing = true)

    @Test fun inboxEmpty() = inbox(Inbox(polledAt = Instant.EPOCH))

    @Test fun inboxNeverPolled() = inbox(Inbox())

    @Test fun inboxErrors() = inbox(
        Inbox(
            campaignsError = ApiError.unreachable(null),
            incidentsError = ApiError.unreachable(null),
            readsIncidents = true
        )
    )

    // Indicators (#862) --------------------------------------------------------------------------

    private inline fun <reified T> example(service: String, name: String): Answer<T> =
        Answer.Body(json.decodeFromString<T>(contractExample(service, name)))

    private fun indicators(): Indicators {
        val kpi = "analytics-kpis"
        return Indicators(
            from = LocalDate.parse("2026-10-18"),
            to = LocalDate.parse("2026-11-16"),
            sales = example<KpiReport>(kpi, "net-sales-store-scoped.json"),
            conversion = example<KpiReport>(kpi, "conversion.json"),
            customers = example<KpiReport>(kpi, "spend.json"),
            stock = example<DemandCoverageReport>(kpi, "demand-coverage.json"),
            campaigns = example<PromotionPage>("campaigns", "promotions.json"),
            definitions = example<KpiDefinitions>(kpi, "kpi-definitions.json"),
            categoryNames = mapOf(4 to "Footwear")
        )
    }

    private fun indicators(
        state: IndicatorsState,
        period: IndicatorPeriod = IndicatorPeriod.LAST_30,
        before: () -> Unit = {}
    ) = audit(before) { IndicatorsContent(state, period, "Coyoacan", {}, {}) }

    @Test fun indicatorsShown() = indicators(IndicatorsState.Shown(indicators()))

    @Test fun indicatorsLoading() = indicators(IndicatorsState.Loading, IndicatorPeriod.LAST_7)

    @Test fun indicatorsStoreRefused() = indicators(
        IndicatorsState.StoreRefused(ApiError(403, "forbidden_store", "Outside your stores.", "corr-1"))
    )

    @Test fun indicatorsEachReportRefused() {
        val refused = Answer.Refused(ApiError.unreachable(null))
        indicators(
            IndicatorsState.Shown(
                indicators().copy(
                    sales = refused,
                    conversion = refused,
                    customers = refused,
                    stock = refused,
                    campaigns = refused,
                    definitions = refused
                )
            ),
            IndicatorPeriod.LAST_90
        )
    }

    @Test fun indicatorsDefinitionOpen() {
        indicators(
            IndicatorsState.Shown(indicators()),
            before = { compose.onNodeWithTag("figure-net-sales-net_amount").performScrollTo().performClick() }
        )
        compose.onNodeWithTag("definition").assertExists()
    }

    // Catalog search and availability (#858) -----------------------------------------------------

    private fun catalog(state: CatalogSearch) = audit {
        Column(Modifier.verticalScroll(rememberScrollState())) { CatalogSearchSection(state, {}, {}) }
    }

    @Test fun catalogIdle() = catalog(CatalogSearch.Idle)

    @Test fun catalogSearching() = catalog(CatalogSearch.Searching)

    @Test fun catalogFound() = catalog(
        CatalogSearch.Found("sock", listOf(socks, shirt, Product(3, "SKU3", "Old cap", 1, "9.99", false)), total = 25)
    )

    @Test fun catalogFoundNothing() = catalog(CatalogSearch.Found("zzz", emptyList(), total = 0))

    @Test fun catalogFailed() = catalog(CatalogSearch.Failed(ApiError.unreachable(null)))

    private fun answer(result: ScanResult, onAdd: ((Product) -> Unit)? = {}) = audit {
        Column(Modifier.verticalScroll(rememberScrollState())) { ScanAnswer(result, noop, noop, noop, onAdd) }
    }

    private fun shown(status: String?, inStore: Availability?, quantity: Int? = 14, product: Product = shirt) =
        ScanResult.Shown(product, "Coyoacan", quantity, null, null, status, inStore)

    @Test fun availabilitySellable() = answer(shown("active", Availability(3, true, "AVAILABLE", 14)))

    @Test fun availabilitySellableReadOnly() = answer(
        shown("active", Availability(3, true, "AVAILABLE", 14)),
        onAdd = null
    )

    @Test fun availabilityOutOfStock() = answer(
        shown("active", Availability(3, false, "OUT_OF_STOCK", 0), quantity = 0)
    )

    @Test fun availabilityNotStocked() = answer(shown("active", null, quantity = null))

    @Test fun availabilityChannelNotEnabled() = answer(
        shown("active", Availability(3, false, "CHANNEL_NOT_ENABLED", null))
    )

    @Test fun availabilityPaused() = answer(shown("paused", Availability(3, false, "NOT_SELLABLE", null)))

    @Test fun availabilityDiscontinued() = answer(shown("discontinued", Availability(3, false, "NOT_SELLABLE", null)))

    @Test fun availabilityRetiredProduct() =
        answer(shown(null, null, product = Product(3, "SKU3", "Old cap", 1, "9.99", false)))

    @Test fun availabilityStockError() =
        answer(ScanResult.Shown(shirt, "Coyoacan", null, ApiError.unreachable(null), null, "active", null))

    @Test fun availabilityBarcodeNotFound() = answer(ScanResult.BarcodeNotFound("7501234567890"))

    @Test fun availabilityInvalidBarcode() = answer(ScanResult.InvalidBarcode)

    // Cart, summary and receipt (#859) -----------------------------------------------------------

    private val winter = Promotion(51, 12, "Winter 20", "20.00", "2026-11-01", "2026-11-30", listOf(1), listOf(1), true)
    private val discounts = DiscountState(
        storeId = 1,
        channelId = 1,
        promotions = listOf(winter),
        maxDiscountPercent = "15.00",
        loaded = true
    )

    private fun cart(
        reviewing: Boolean = false,
        queuedRowId: Long? = null,
        confirming: Boolean = false,
        error: ApiError? = null
    ) = SaleBuilderState(
        lines = listOf(SaleLine(shirt, 2, winter), SaleLine(socks, 1)),
        discounts = discounts,
        reviewing = reviewing,
        queuedRowId = queuedRowId,
        confirming = confirming,
        error = error
    )

    private fun ready(sale: SaleBuilderState, queued: OutboxRow? = null, history: List<OutboxRow> = emptyList()) =
        SaleUi(
            customer = ana,
            store = ChosenStore(1, "Centro"),
            registration = ready,
            sale = sale,
            queued = queued,
            history = history
        )

    private val sentSale =
        """{"customer_id":"c1","store_id":1,"channel_id":1,"occurred_at":"2026-11-18T12:00:00Z",""" +
            """"lines":[{"product_id":2,"quantity":2,"unit_price":"34.00","promotion_id":51},""" +
            """{"product_id":1,"quantity":1,"unit_price":"12.50"}]}"""

    private val recorded = row(7, Operation.SALE, RowState.DONE).copy(
        body = sentSale,
        status = 201,
        responseBody = """{"sale_id":4812,"customer_id":"c1","store_id":1,"channel_id":1,""" +
            """"occurred_at":"2026-11-18T18:00:00Z","total":"80.50",""" +
            """"lines":[{"product_id":2,"quantity":2,"unit_price":"34.00"},""" +
            """{"product_id":1,"quantity":1,"unit_price":"12.50"}]}"""
    )

    private val refusedSale = row(8, Operation.SALE, RowState.REFUSED).copy(
        body = sentSale,
        status = 422,
        code = "discount_above_promotion",
        responseBody = """{"code":"discount_above_promotion","message":"x",""" +
            """"details":[{"field":"lines[0].unit_price","code":"below_promotion"}]}"""
    )

    private val queuedSale = row(9, Operation.SALE, RowState.PENDING).copy(body = sentSale)

    private fun review(ui: SaleUi) = audit { ReviewContent(ui, SaleActions()) }

    @Test fun reviewSummary() = review(ready(cart(reviewing = true)))

    @Test fun reviewConfirming() = review(ready(cart(reviewing = true, confirming = true)))

    @Test fun reviewRefused() = review(
        ready(cart(reviewing = true, error = ApiError(422, "validation_failed", "The sale has no items.", "c-3")))
    )

    private fun outcome(ui: SaleUi) = audit {
        Column(Modifier.verticalScroll(rememberScrollState())) { SaleOutcome(ui) }
    }

    @Test fun receiptRecorded() = outcome(ready(cart(queuedRowId = 7), queued = recorded))

    @Test fun receiptReplayed() = outcome(
        ready(cart(queuedRowId = 7), queued = recorded.copy(code = "idempotent_replayed"))
    )

    @Test fun receiptRefusedNamesTheLine() = outcome(ready(cart(queuedRowId = 8), queued = refusedSale))

    private fun bar(ui: SaleUi) = audit { SaleBar(ui, SaleActions()) }

    @Test fun barReady() = bar(ready(cart()))

    @Test fun barBlocked() = bar(SaleUi(registration = ready, sale = cart()))

    @Test fun barConfirming() = bar(ready(cart(confirming = true)))

    @Test fun barQueued() = bar(ready(cart(queuedRowId = 9), queued = queuedSale))

    @Test fun barRecorded() = bar(ready(cart(queuedRowId = 7), queued = recorded))

    @Test fun barRefused() = bar(ready(cart(queuedRowId = 8), queued = refusedSale))

    private fun history(ui: SaleUi) = audit {
        Column(Modifier.verticalScroll(rememberScrollState())) { HistorySection(ui, SaleActions()) }
    }

    @Test fun historyMixed() = history(ready(cart(), history = listOf(queuedSale, recorded, refusedSale)))

    @Test fun historyEmpty() = history(ready(cart()))

    @Test fun historyStatusMessages() = history(
        ready(cart(), history = listOf(recorded)).copy(
            statusMessage = "The sale was recorded as sale 4812.",
            statusError = ApiError.unreachable(null)
        )
    )

    @Test fun saleWithPromotionLineAndHistory() = sale(
        ready(cart(), history = listOf(queuedSale, recorded, refusedSale))
    )

    @Test fun saleRecordedWithReceipt() = sale(
        ready(cart(queuedRowId = 7), queued = recorded, history = listOf(recorded))
    )

    @Test fun saleRefusedWithKeptCart() =
        sale(ready(cart(queuedRowId = 8), queued = refusedSale, history = listOf(refusedSale)))

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
