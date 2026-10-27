package online.maxthecoder.mosaiq.floor.ui.screens

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.time.Instant
import java.time.ZoneId
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Store
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.ui.FloorApp
import online.maxthecoder.mosaiq.floor.ui.SessionState
import online.maxthecoder.mosaiq.floor.ui.StoresState
import online.maxthecoder.mosaiq.floor.ui.checkin.CheckInContent
import online.maxthecoder.mosaiq.floor.ui.checkin.CheckInStatus
import online.maxthecoder.mosaiq.floor.ui.checkin.StorePickerContent
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxContent
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** The redesigned sign-in, check-in, store picker and outbox, state by state (UX-06, UX-07, UX-11). */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class FloorScreensTest {
    @get:Rule val compose = createComposeRule()

    private fun show(content: @Composable () -> Unit) =
        compose.setContent { MosaiqTheme(darkTheme = false) { Column { Box(Modifier.height(700.dp)) { content() } } } }

    private fun session(state: SessionState, signIn: (String, String) -> Unit = { _, _ -> }) = show {
        FloorApp(state = state, onSignIn = signIn, onRetry = {}, onSignOut = {})
    }

    @Test
    fun signInSubmitsFromTheKeyboardAndShowsAFriendlyCredentialsError() {
        var sent: Pair<String, String>? = null
        session(
            SessionState.SignIn(error = ApiError(401, "invalid_credentials", "raw", "corr-1")),
            signIn = { e, p -> sent = e to p }
        )
        compose.onNodeWithText("That email or password is not right. Check them and try again.").assertExists()
        compose.onNodeWithText("raw").assertDoesNotExist()
        compose.onNodeWithText("Details").assertExists()
        compose.onNodeWithText("Corr", substring = true).assertDoesNotExist()
        compose.onAllNodesWithText("Sign in")[0].assertIsNotEnabled()
        compose.onAllNodes(hasSetTextAction())[0].performTextInput("ana@mosaiq-demo.com")
        compose.onAllNodes(hasSetTextAction())[1].performTextInput("secret")
        compose.onAllNodes(hasSetTextAction())[1].performImeAction()
        assertEquals("ana@mosaiq-demo.com" to "secret", sent)
    }

    @Test
    fun anUnreachableServerIsSaidInPlainWords() {
        session(SessionState.SignIn(error = ApiError.unreachable(null)))
        compose.onNodeWithText("MOSAIQ could not be reached. Check your connection and try again.").assertExists()
    }

    @Test
    fun theSessionLoadingStateFillsTheAreaWithALabel() {
        session(SessionState.Loading)
        compose.onNodeWithText("Loading your account…").assertIsDisplayed()
    }

    @Test
    fun aFailedSessionOffersRetryAndSignOut() {
        session(SessionState.Failed(ApiError.unreachable(null)))
        compose.onNodeWithText("Retry").assertExists()
        compose.onNodeWithText("Sign out").assertExists()
    }

    @Test
    fun checkInStatesUsePlainBadgesAndNeverShowIds() {
        var status: CheckInStatus by mutableStateOf<CheckInStatus>(CheckInStatus.NotCheckedIn)
        var checkIns = 0
        show {
            CheckInContent("Centro", "Monterrey, NL", status, null, null, false, { checkIns++ }, {}, {})
        }
        compose.onNodeWithText("Centro").assertExists()
        compose.onNodeWithText("Monterrey, NL").assertExists()
        compose.onNodeWithText("Not checked in").assertExists()
        compose.onNodeWithText("Check in here").performClick()
        assertEquals(1, checkIns)
        compose.onNodeWithText("Change store").assertExists()

        compose.runOnUiThread { status = CheckInStatus.Waiting }
        compose.onNodeWithText("Waiting to send").assertExists()
        compose.runOnUiThread { status = CheckInStatus.Done("14:32") }
        compose.onNodeWithText("Checked in at 14:32").assertExists()
        compose.runOnUiThread { status = CheckInStatus.Done("14:32", verified = false) }
        compose.onNodeWithText("Checked in at 14:32, not verified").assertExists()
        compose.runOnUiThread { status = CheckInStatus.Sending }
        compose.onNodeWithText("Finding your position…").assertExists()
        compose.runOnUiThread { status = CheckInStatus.NeedsAttention(ApiError(422, "x", "Too far away.", "c-9")) }
        compose.onNodeWithText("Needs attention").assertExists()
        compose.onNodeWithText("Too far away.").assertExists()
        compose.onNodeWithText("c-9", substring = true).assertDoesNotExist()
    }

    @Test
    fun aDeniedLocationExplainsWhyAndOpensSettings() {
        var opened = 0
        show {
            CheckInContent(
                "Centro",
                null,
                CheckInStatus.Waiting,
                null,
                "Location is off for this app.",
                true,
                {},
                {},
                { opened++ }
            )
        }
        compose.onNodeWithText("Location is off for this app.").assertExists()
        compose.onNodeWithText("Open settings").performClick()
        assertEquals(1, opened)
    }

    private val stores = listOf(Store(1, "Centro", "Monterrey", "NL", true), Store(2, "Valle", "Monterrey", "NL", true))

    private fun picker(state: StoresState, onPick: (Store) -> Unit = {}, onSearch: () -> Unit = {}) = show {
        StorePickerContent(state, state.city, {}, onSearch, {}, {}, onPick)
    }

    @Test
    fun theStorePickerShowsResultsAndPicksOne() {
        var picked: Store? = null
        picker(StoresState(stores = stores, totalPages = 2), onPick = { picked = it })
        compose.onAllNodesWithText("Monterrey, NL").assertCountEquals(2)
        compose.onNodeWithText("Page 1 of 2").assertExists()
        compose.onNodeWithText("Valle").performClick()
        assertEquals(2, picked?.storeId)
    }

    @Test
    fun theStorePickerSearchIsDisabledWhileLoading() {
        picker(StoresState(loading = true))
        compose.onNodeWithText("Searching stores…").assertExists()
        compose.onNodeWithText("Search").assertIsNotEnabled()
        compose.onNodeWithText("Page 1 of 1").assertExists()
    }

    @Test
    fun theStorePickerExplainsAnEmptyCityAndAnError() {
        picker(StoresState(city = "Atlantis"))
        compose.onNodeWithText("No stores in that city").assertExists()
        compose.onNodeWithText("Search").assertIsEnabled()
    }

    @Test
    fun theStorePickerShowsAnErrorWithRetry() {
        picker(StoresState(error = ApiError.unreachable(null)))
        compose.onNodeWithText("Retry").assertExists()
        compose.onNodeWithText("Page 1 of 1").assertExists()
    }

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

    private fun outbox(rows: List<OutboxRow>, onDismiss: (OutboxRow) -> Unit = {}) = show {
        OutboxContent(rows, "A", mapOf(3 to "Centro"), onDismiss, now, ZoneId.of("UTC"))
    }

    @Test
    fun theOutboxIsCalmWhenEmpty() {
        outbox(emptyList())
        compose.onNodeWithText("Everything is sent").assertExists()
    }

    @Test
    fun outboxRowsShowKindTimeBadgeAndPluralAttempts() {
        outbox(
            listOf(
                row(1, Operation.CHECK_IN, RowState.PENDING, body = """{"store_id":3}""", attempts = 1),
                row(2, Operation.SALE, RowState.PENDING, attempts = 2)
            )
        )
        compose.onNodeWithText("Check-in at Centro").assertExists()
        compose.onNodeWithText("Today, 19:00 · Tried 1 time so far").assertExists()
        compose.onNodeWithText("Today, 19:00 · Tried 2 times so far").assertExists()
        compose.onAllNodesWithText("Waiting to send").assertCountEquals(2)
        compose.onNodeWithText("client-1", substring = true).assertDoesNotExist()
    }

    @Test
    fun aRefusedRowSaysWhyInPlainWordsAndCanBeDismissed() {
        var dismissed: Long? = null
        outbox(listOf(row(5, Operation.SALE, RowState.REFUSED)), onDismiss = { dismissed = it.id })
        compose.onNodeWithText("Needs attention").assertExists()
        compose.onNodeWithText("The sale has no items.").assertExists()
        compose.onNodeWithText("corr-77", substring = true).assertDoesNotExist()
        compose.onNodeWithText("Dismiss").performScrollTo().performClick()
        assertEquals(5L, dismissed)
    }
}
