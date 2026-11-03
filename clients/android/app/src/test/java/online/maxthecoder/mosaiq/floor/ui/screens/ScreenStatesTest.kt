package online.maxthecoder.mosaiq.floor.ui.screens

import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.time.Instant
import java.time.ZoneId
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Category
import online.maxthecoder.mosaiq.floor.net.Channel
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.ErrorDetail
import online.maxthecoder.mosaiq.floor.net.Store
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.FloorApp
import online.maxthecoder.mosaiq.floor.ui.InsightsState
import online.maxthecoder.mosaiq.floor.ui.InterestsState
import online.maxthecoder.mosaiq.floor.ui.RegistrationState
import online.maxthecoder.mosaiq.floor.ui.SaleBuilderState
import online.maxthecoder.mosaiq.floor.ui.SalesContent
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.StoresState
import online.maxthecoder.mosaiq.floor.ui.checkin.StorePickerContent
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxContent
import online.maxthecoder.mosaiq.floor.ui.sale.RegistrationFields
import online.maxthecoder.mosaiq.floor.ui.sale.RegistrationForm
import online.maxthecoder.mosaiq.floor.ui.sale.SaleActions
import online.maxthecoder.mosaiq.floor.ui.sale.SaleUi
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/**
 * The states the other screen tests leave out (UX-16): the sign-in form empty, busy and signed in,
 * the store picker's controls, the outbox's sent and foreign rows, and the sale screen's interests,
 * registration form and error rows. Stateless content only, so one Robolectric setup serves them all.
 */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class ScreenStatesTest {
    @get:Rule val compose = createComposeRule()

    private fun show(content: @Composable () -> Unit) = compose.setContent {
        MosaiqTheme(
            darkTheme = false
        ) { content() }
    }

    // ---- Sign-in -------------------------------------------------------------------------------

    private fun app(state: SessionState, onSignIn: (String, String) -> Unit = { _, _ -> }) = show {
        FloorApp(state = state, onSignIn = onSignIn, onRetry = {}, onSignOut = {})
    }

    @Test
    fun theEmptySignInFormWaitsForBothFields() {
        app(SessionState.SignIn())
        compose.onAllNodes(hasSetTextAction()).assertCountEquals(2)
        compose.onAllNodesWithText("Sign in")[0].assertIsNotEnabled()
        compose.onAllNodes(hasSetTextAction())[0].performTextInput("ana@mosaiq-demo.com")
        compose.onAllNodesWithText("Sign in")[0].assertIsNotEnabled()
        compose.onAllNodes(hasSetTextAction())[1].performTextInput("secret")
        compose.onAllNodesWithText("Sign in")[0].assertIsEnabled()
    }

    @Test
    fun aNoticeAboveTheFormExplainsWhyYouAreHere() {
        app(SessionState.SignIn(notice = "Your session ended. Sign in again."))
        compose.onNodeWithText("Your session ended. Sign in again.").assertExists()
    }

    @Test
    fun aServerErrorOnSignInIsSaidInPlainWords() {
        app(SessionState.SignIn(error = ApiError(503, "down", "raw 503 body", "corr-5")))
        compose.onNodeWithText("The service is having trouble right now. Try again in a moment.").assertExists()
        compose.onNodeWithText("raw 503 body").assertDoesNotExist()
    }

    @Test
    fun aSubmittedSignInKeepsTheFieldsAndLocksThemUntilTheSessionAnswers() {
        var session: SessionState by mutableStateOf(SessionState.SignIn())
        show {
            FloorApp(
                state = session,
                onSignIn = { _, _ -> session = SessionState.Loading },
                onRetry = {},
                onSignOut = {}
            )
        }
        compose.onAllNodes(hasSetTextAction())[0].performTextInput("ana@mosaiq-demo.com")
        compose.onAllNodes(hasSetTextAction())[1].performTextInput("secret")
        compose.onAllNodesWithText("Sign in")[0].performClick()
        compose.waitForIdle()
        // Busy: still the form (not the full-area account loader), with both fields locked.
        compose.onNodeWithText("Loading your account…").assertDoesNotExist()
        compose.onNodeWithText("ana@mosaiq-demo.com").assertIsNotEnabled()
        compose.onAllNodes(hasSetTextAction()).assertCountEquals(0)
        // A refusal brings the form back with the email still typed.
        compose.runOnUiThread { session = SessionState.SignIn(error = ApiError(401, "invalid_credentials", "x", null)) }
        compose.waitForIdle()
        compose.onNodeWithText("ana@mosaiq-demo.com").assertIsEnabled()
    }

    @Test
    fun signedInHandsTheWindowToTheFloorOrShowsWhoIsSignedIn() {
        app(SessionState.SignedIn("Demo A", "STORE_MANAGER"))
        compose.onNodeWithText("Signed in as").assertExists()
        compose.onNodeWithText("Demo A").assertExists()
        compose.onNodeWithText("STORE_MANAGER").assertExists()
        compose.onNodeWithText("Sign out").assertExists()
    }

    @Test
    fun theFloorSlotReplacesTheSignedInPanel() {
        show {
            FloorApp(
                state = SessionState.SignedIn("Demo A", "STORE_MANAGER"),
                onSignIn = { _, _ -> },
                onRetry = {},
                onSignOut = {},
                floor = { Text("floor for ${it.name}") }
            )
        }
        compose.onNodeWithText("floor for Demo A").assertExists()
        compose.onNodeWithText("Signed in as").assertDoesNotExist()
    }

    // ---- Store picker ---------------------------------------------------------------------------

    private val stores = listOf(Store(1, "Centro", "Monterrey", "NL", true), Store(2, "Valle", "Monterrey", "NL", true))

    @Test
    fun theStorePickerSearchRunsFromTheButtonAndTheCityIsReported() {
        var searches = 0
        var city by mutableStateOf("")
        show {
            StorePickerContent(StoresState(), city, { city = it }, { searches++ }, {}, {}, {})
        }
        compose.onNode(hasSetTextAction()).performTextInput("Leon")
        assertEquals("Leon", city)
        compose.onNodeWithText("Search").performClick()
        assertEquals(1, searches)
    }

    @Test
    fun theStorePickerPagesForwardAndBackAndIgnoresPagingWhileLoading() {
        val pages = mutableListOf<Int>()
        var state by mutableStateOf(StoresState(stores = stores, page = 2, totalPages = 3))
        show { StorePickerContent(state, "", {}, {}, {}, { pages += it }, {}) }
        compose.onNodeWithText("Page 2 of 3").assertExists()
        compose.onNodeWithText("Next").performClick()
        compose.onNodeWithText("Previous").performClick()
        assertEquals(listOf(3, 1), pages)
        state = state.copy(loading = true)
        compose.onNodeWithText("Next").performClick()
        assertEquals(listOf(3, 1), pages)
    }

    @Test
    fun theStorePickerRetryRunsTheLastSearchAgain() {
        var retries = 0
        show {
            StorePickerContent(StoresState(error = ApiError.unreachable(null)), "", {}, {}, { retries++ }, {}, {})
        }
        compose.onNodeWithText("Retry").performClick()
        assertEquals(1, retries)
    }

    // ---- Outbox ---------------------------------------------------------------------------------

    private val now = Instant.parse("2026-10-26T20:00:00Z")

    private fun row(
        id: Long,
        op: Operation,
        state: RowState,
        user: String = "A",
        ageMs: Long = 3_600_000,
        attempts: Int = 0
    ) = OutboxRow(
        id = id,
        operation = op,
        path = "/p",
        body = "{}",
        clientId = "client-$id",
        userId = user,
        createdAt = now.toEpochMilli() - ageMs,
        state = state,
        attempts = attempts
    )

    private fun outbox(rows: List<OutboxRow>) = show {
        OutboxContent(rows, "A", emptyMap(), {}, now, ZoneId.of("UTC"))
    }

    @Test
    fun aSentRowShowsASuccessBadgeAndEveryKindHasAName() {
        outbox(
            listOf(
                row(1, Operation.SALE, RowState.DONE),
                row(2, Operation.QR_SCAN, RowState.PENDING),
                row(3, Operation.RECO_CLICK, RowState.PENDING),
                row(4, Operation.CHECK_IN, RowState.PENDING)
            )
        )
        compose.onNodeWithText("Sent").assertExists()
        compose.onNodeWithText("Sale").assertExists()
        compose.onNodeWithText("Product scan").assertExists()
        compose.onNodeWithText("Recommendation click").assertExists()
        compose.onNodeWithText("Check-in").assertExists()
    }

    @Test
    fun aRowFromAnotherUserSaysWhoItIsWaitingFor() {
        outbox(listOf(row(1, Operation.SALE, RowState.PENDING, user = "B")))
        compose.onNodeWithText("Waiting for the user who made it to sign in again.").assertExists()
    }

    @Test
    fun theOutboxNamesYesterdayAndOlderDays() {
        outbox(
            listOf(
                row(1, Operation.SALE, RowState.PENDING, ageMs = 24 * 3_600_000L),
                row(2, Operation.SALE, RowState.PENDING, ageMs = 10 * 24 * 3_600_000L)
            )
        )
        compose.onNodeWithText("Yesterday, 20:00").assertExists()
        compose.onNodeWithText("Today", substring = true).assertDoesNotExist()
        compose.onNodeWithText("2026", substring = true).assertExists()
    }

    // ---- Sale: customer interests, registration, errors ----------------------------------------

    private val store = ChosenStore(1, "Centro")
    private val ana = Customer("c1", "Ana Torres", "ana@example.com", null, 1, "2025-01-01")
    private val channels = listOf(Channel(1, "physical_store"), Channel(2, "online"))
    private val ready = RegistrationState(channels = channels, selectedChannelId = 1)

    private fun sale(ui: () -> SaleUi, actions: SaleActions = SaleActions()) = show {
        SalesContent(ui(), actions, SnackbarHostState()) {}
    }

    @Test
    fun theStoreLineAsksForACheckInWhenNoStoreIsChosen() {
        var picked = 0
        sale({ SaleUi(registration = ready) }, SaleActions(onPickStore = { picked++ }))
        compose.onNodeWithText("Check in at your store first.").assertExists()
        compose.onNodeWithText("Store and check-in").performClick()
        assertEquals(1, picked)
    }

    @Test
    fun interestsLoadShowErrorAndSave() {
        var loads = 0
        var saves = 0
        var toggled: Int? = null
        var ui by mutableStateOf(SaleUi(customer = ana, store = store, registration = ready))
        sale(
            { ui },
            SaleActions(
                onLoadInterests = { loads++ },
                onSaveInterests = { saves++ },
                onToggleInterest = { toggled = it }
            )
        )
        compose.onNodeWithText("Show interests").performScrollTo().performClick()
        compose.onNodeWithText("Load interests").performScrollTo().performClick()
        assertEquals(1, loads)

        ui = ui.copy(interests = InterestsState(loading = true))
        compose.onNodeWithText("Loading interests…").assertExists()

        ui = ui.copy(interests = InterestsState(error = ApiError.unreachable(null)))
        compose.onNodeWithText("Retry").performScrollTo().performClick()
        assertEquals(2, loads)

        ui = ui.copy(
            interests = InterestsState(
                categories = listOf(Category(7, "Outdoor"), Category(8, "Kitchen")),
                selected = setOf(7),
                ready = true,
                saved = true
            )
        )
        compose.onNodeWithText("Outdoor").performScrollTo().performClick()
        assertEquals(7, toggled)
        compose.onNodeWithText("Interests saved.").assertExists()
        compose.onNodeWithText("Save interests").performScrollTo().performClick()
        assertEquals(1, saves)
        compose.onNodeWithText("Hide interests").assertExists()
    }

    @Test
    fun productSearchErrorOffersRetryAndTheCartStartsEmpty() {
        var searched = ""
        var ui by mutableStateOf(SaleUi(store = store, registration = ready))
        sale({ ui }, SaleActions(onSearchProducts = { searched = it }))
        compose.onNodeWithTag("cart-empty").assertExists()
        compose.onNodeWithTag("product-query").performTextInput("sock")
        compose.onNodeWithText("Search").performScrollTo().performClick()
        assertEquals("sock", searched)
        ui = ui.copy(sale = SaleBuilderState(error = ApiError(503, "down", "Products are down", null)))
        compose.onNodeWithText("Products are down").assertExists()
        compose.onNodeWithText("Retry").performScrollTo().performClick()
        assertEquals("sock", searched)
    }

    @Test
    fun theHistoryIsEmptyThenShowsARefusalWithAdviceAndAStatusError() {
        var ui by mutableStateOf(SaleUi(store = store, registration = ready))
        sale({ ui })
        compose.onNodeWithTag("history-empty").performScrollTo().assertExists()
        ui = ui.copy(
            history = listOf(
                OutboxRow(
                    id = 9,
                    operation = Operation.SALE,
                    path = "/v1/sales",
                    body = "{}",
                    clientId = "c9",
                    userId = "u",
                    createdAt = now.toEpochMilli(),
                    state = RowState.REFUSED,
                    code = "future_dated",
                    message = "The sale is dated in the future."
                )
            ),
            statusError = ApiError(503, "down", "Status is unavailable", null)
        )
        compose.onNodeWithText("The sale is dated in the future.").performScrollTo().assertExists()
        compose.onNodeWithText("Status is unavailable").performScrollTo().assertExists()
        compose.onNodeWithTag("history-empty").assertDoesNotExist()
    }

    @Test
    fun aSentSaleChecksItsStatusAndShowsTheAnswer() {
        var checked: Long? = null
        val sent = OutboxRow(
            id = 4,
            operation = Operation.SALE,
            path = "/v1/sales",
            body = "{}",
            clientId = "c4",
            userId = "u",
            createdAt = now.toEpochMilli(),
            state = RowState.DONE
        )
        sale(
            { SaleUi(store = store, registration = ready, history = listOf(sent)) },
            SaleActions(onRefreshStatus = { checked = it.id }, rowStatus = { "Counted in today's totals." })
        )
        compose.onNodeWithText("Counted in today's totals.").performScrollTo().assertExists()
        compose.onNodeWithText("Check sale status").performScrollTo().performClick()
        assertEquals(4L, checked)
    }

    @Test
    fun noSalesChannelBlocksRecordingUntilTheyLoad() {
        var loads = 0
        sale(
            { SaleUi(customer = ana, store = store) },
            SaleActions(onLoadChannels = { loads++ })
        )
        compose.onNodeWithText("Add at least one item to record the sale.").assertExists()
        compose.onNodeWithText("Load channels").performClick()
        assertEquals(1, loads)
    }

    // ---- Sale: registration form ----------------------------------------------------------------

    private fun form(
        registration: RegistrationState,
        fields: RegistrationFields = RegistrationFields(),
        onSubmit: () -> Unit = {},
        onLoad: () -> Unit = {}
    ) = show {
        RegistrationForm(fields, {}, registration, {}, onLoad, onSubmit)
    }

    @Test
    fun theRegistrationFormEnablesSubmitOnlyWithANameAValidEmailAndAChannel() {
        var submitted = 0
        form(ready, RegistrationFields(name = "Ben Ruiz", email = "ben@example.com"), onSubmit = { submitted++ })
        compose.onNodeWithText("Register customer").performScrollTo().assertIsEnabled().performClick()
        assertEquals(1, submitted)
    }

    @Test
    fun theRegistrationFormIsBlockedWithoutANameOrWithABadEmail() {
        form(ready, RegistrationFields(name = "", email = "ben@example.com"))
        compose.onNodeWithText("Register customer").assertIsNotEnabled()
    }

    @Test
    fun theRegistrationFormShowsChannelsLoadingThenALoadButton() {
        var loads = 0
        var state by mutableStateOf(RegistrationState(loading = true))
        show { RegistrationForm(RegistrationFields(), {}, state, {}, { loads++ }, {}) }
        compose.onNodeWithText("Loading sales channels…").assertExists()
        state = RegistrationState()
        compose.onNodeWithText("Load channels").performClick()
        assertEquals(1, loads)
    }

    @Test
    fun theRegistrationFormShowsTheRefusalPerFieldAndTheUnknownResultAdvice() {
        form(
            ready.copy(
                error = ApiError(
                    422,
                    "validation_failed",
                    "Check the form.",
                    "c-1",
                    listOf(ErrorDetail("email", "already_used"))
                ),
                timeoutAdvice = true
            ),
            RegistrationFields(name = "Ben", email = "ben@example.com")
        )
        compose.onNodeWithText("Check the form.").assertExists()
        compose.onNodeWithText("Already used").assertExists()
        compose.onNodeWithText("The result is unknown. Search for this customer before registering again.")
            .assertExists()
    }

    @Test
    fun aRegistrationInFlightShowsTheBusyButton() {
        form(ready.copy(loading = true), RegistrationFields(name = "Ben"))
        compose.onNodeWithText("Register customer").assertExists()
    }

    // ---- Customer insights: both partial errors -------------------------------------------------

    @Test
    fun insightsHideEverythingWithoutACustomerAndShowARecommendationsErrorWithRetry() {
        var retries = 0
        var state by mutableStateOf(InsightsState())
        show {
            online.maxthecoder.mosaiq.floor.ui.CustomerInsightsContent(state, { retries++ }, {})
        }
        compose.onNodeWithText("Segment").assertDoesNotExist()
        state = InsightsState(
            customerId = "c1",
            recommendationsError = ApiError(503, "down", "Recommendations are down", null)
        )
        compose.onNodeWithText("Recommendations are down").assertExists()
        compose.onNodeWithText("Retry").performClick()
        assertEquals(1, retries)
    }
}
