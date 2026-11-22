package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import online.maxthecoder.mosaiq.floor.AppText
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.contract
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.CaptureIncident
import online.maxthecoder.mosaiq.floor.net.CaptureIncidentPage
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.ui.review.ACKNOWLEDGED
import online.maxthecoder.mosaiq.floor.ui.review.IncidentsState
import online.maxthecoder.mosaiq.floor.ui.review.OPEN
import online.maxthecoder.mosaiq.floor.ui.review.PromotionsShownState
import online.maxthecoder.mosaiq.floor.ui.review.RESOLVED
import online.maxthecoder.mosaiq.floor.ui.review.ReviewContent
import online.maxthecoder.mosaiq.floor.ui.review.ShownPromotion
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** The supervisor's review screen (F27-14), drawn from the contract's own incidents. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class ReviewContentTest {
    @get:Rule val compose = createComposeRule()

    private val incidents: List<CaptureIncident> =
        json.decodeFromString(
            CaptureIncidentPage.serializer(),
            contract("engagement/examples/capture-incidents-page.json")
        ).captureIncidents
    private val open = incidents.first { it.status == OPEN }
    private val resolved = incidents.first { it.status == RESOLVED }
    private val reviews = mutableListOf<Triple<String, String, String>>()
    private val now = Instant.parse("2026-11-21T20:00:00Z")

    private fun row(id: Long, state: RowState, code: String? = null) = OutboxRow(
        id = id,
        operation = Operation.SALE,
        path = "/p",
        body = "{}",
        clientId = "c$id",
        userId = "S",
        createdAt = now.toEpochMilli() - 3_600_000,
        state = state,
        status = if (state == RowState.REFUSED) 409 else null,
        code = code,
        message = if (state == RowState.REFUSED) "Refused by the service." else null
    )

    private fun show(
        incidents: IncidentsState = IncidentsState(loading = false, incidents = this.incidents, total = 2),
        promotions: PromotionsShownState = PromotionsShownState(loading = false),
        syncs: List<OutboxRow>? = emptyList(),
        dismissed: MutableList<Long> = mutableListOf()
    ) = compose.setContent {
        MosaiqTheme(darkTheme = false) {
            ReviewContent(
                incidents,
                promotions,
                syncs,
                currentUserId = "S",
                sending = emptySet(),
                onReview = { incident, status, note -> reviews += Triple(incident.incidentId, status, note) },
                onRetryIncidents = {},
                onRetryPromotions = {},
                onDismiss = { dismissed += it.id },
                onSendNow = {},
                now = now,
                zone = ZoneId.of("UTC")
            )
        }
    }

    @Test
    fun incidentsAreListedByStateEachWithItsStoreReasonAndReviews() {
        show()
        compose.onNodeWithTag("review-group-OPEN").assertIsDisplayed()
        compose.onNodeWithText("Open (1)").assertIsDisplayed()
        compose.onNodeWithTag("review-group-RESOLVED").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Store 3 · Sale refused").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText(
            "Reason: Stock unavailable",
            substring = true
        ).performScrollTo().assertExists()
        compose.onNodeWithText("3 attempts", substring = true).performScrollTo().assertExists()
        // The resolved one shows its reviews and the supervisor's note, and offers nothing more.
        compose.onNodeWithText("Reviewed with the associate; the badge was scanned outside the store.")
            .performScrollTo().assertIsDisplayed()
        compose.onNodeWithTag("review-resolved-${resolved.incidentId}").assertDoesNotExist()
        compose.onNodeWithTag("review-acknowledged-${resolved.incidentId}").assertDoesNotExist()
    }

    @Test
    fun anOpenIncidentOffersAcknowledgeAndResolveWithTheNoteTyped() {
        show()
        compose.onNodeWithText("Note (optional, up to 200 characters)").performScrollTo().performTextInput("On it")
        compose.onNodeWithTag("review-acknowledged-${open.incidentId}").performScrollTo().performClick()
        compose.onNodeWithTag("review-resolved-${open.incidentId}").performScrollTo().performClick()

        assertEquals(
            listOf(
                Triple(open.incidentId, ACKNOWLEDGED, "On it"),
                Triple(open.incidentId, RESOLVED, "On it")
            ),
            reviews
        )
    }

    @Test
    fun anAcknowledgedIncidentOffersOnlyResolve() {
        val acknowledged = open.copy(status = ACKNOWLEDGED)
        show(IncidentsState(loading = false, incidents = listOf(acknowledged), total = 1))
        compose.onNodeWithTag("review-resolved-${acknowledged.incidentId}").performScrollTo().assertIsDisplayed()
        compose.onNodeWithTag("review-acknowledged-${acknowledged.incidentId}").assertDoesNotExist()
        compose.onNodeWithText("Acknowledged (1)").assertExists()
    }

    @Test
    fun aReviewOnItsWayCannotBeSentTwice() {
        show(IncidentsState(loading = false, incidents = listOf(open), total = 1, busy = setOf(open.incidentId)))
        compose.onNodeWithTag("review-resolved-${open.incidentId}").performScrollTo().assertIsNotEnabled()
        compose.onNodeWithTag("review-acknowledged-${open.incidentId}").assertIsNotEnabled()
    }

    @Test
    fun aRefusedReviewShowsItsReasonBesideTheIncident() {
        val refusal = ApiError(409, "invalid_transition", "An incident only moves forward.", "corr-1")
        show(
            IncidentsState(
                loading = false,
                incidents = listOf(open),
                total = 1,
                reviewErrors = mapOf(open.incidentId to refusal)
            )
        )
        compose.onNodeWithTag("review-error-${open.incidentId}").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Someone has already moved this incident, so nothing was changed.", substring = true)
            .assertExists()
        compose.onNodeWithText("An incident only moves forward.").assertDoesNotExist()
    }

    @Test
    fun aRefusedForbiddenStoreIsInPlainWords() {
        val refusal = ApiError(403, "forbidden_store", "The caller is not assigned to this store.", "corr-2")
        show(
            IncidentsState(
                loading = false,
                incidents = listOf(open),
                total = 1,
                reviewErrors = mapOf(open.incidentId to refusal)
            )
        )
        compose.onNodeWithTag("review-error-${open.incidentId}").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText(AppText.get(R.string.review_error_forbidden_store)).assertExists()
    }

    @Test
    fun loadingEmptyAndFailedListsAreSaidInWords() {
        show(IncidentsState(loading = true))
        compose.onNodeWithText("Loading incidents…").assertExists()
    }

    @Test
    fun noIncidentIsSaidInPlainWords() {
        show(IncidentsState(loading = false))
        compose.onNodeWithTag("review-incidents-none").assertExists()
    }

    @Test
    fun aFailedListOffersARetryAndAListCutAtThePageLimitSaysSo() {
        show(IncidentsState(loading = false, error = ApiError.unreachable(null)))
        compose.onNodeWithText("No connection.", substring = true).performScrollTo().assertExists()
        compose.onNodeWithText("Retry").assertExists()
    }

    @Test
    fun aLongListSaysHowManyThereAre() {
        show(IncidentsState(loading = false, incidents = incidents, total = 450))
        compose.onNodeWithText("Showing the newest 2 of 450.").performScrollTo().assertExists()
    }

    @Test
    fun theSyncsOfThisPhoneShowTheirStateAndTheirReason() {
        val dismissed = mutableListOf<Long>()
        show(
            syncs = listOf(row(1, RowState.PENDING), row(2, RowState.REFUSED, code = "stock_unavailable")),
            dismissed = dismissed
        )
        compose.onNodeWithText("1 write waiting to send", substring = true).performScrollTo().assertExists()
        compose.onNodeWithText("Waiting to send").performScrollTo().assertExists()
        compose.onNodeWithText("Needs attention").performScrollTo().assertExists()
        compose.onNodeWithTag("review-syncs").assertExists()
        compose.onAllNodesWithText("The store does not have enough units", substring = true)
            .assertCountEquals(1)
        compose.onNodeWithText("Dismiss").performScrollTo().performClick()
        assertEquals(listOf(2L), dismissed)
    }

    @Test
    fun nothingWaitingOnThisPhoneIsSaid() {
        show(syncs = emptyList())
        compose.onNodeWithTag("review-syncs-none").performScrollTo().assertExists()
    }

    @Test
    fun promotionsShownAreListedByStoreWithNoCustomerAndSayWhatTheyAre() {
        show(
            promotions =
            PromotionsShownState(
                loading = false,
                rows =
                listOf(
                    ShownPromotion(3, 12, "Loyal customers", 2),
                    ShownPromotion(3, 14, null, 1),
                    ShownPromotion(5, 12, "Loyal customers", 1)
                ),
                since = LocalDate.parse("2026-11-15"),
                counted = 4,
                total = 4,
                storeNames = mapOf(3 to "Coyoacan", 5 to "Centro")
            )
        )
        compose.onNodeWithTag("review-store-3").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Coyoacan").assertExists()
        compose.onNodeWithText("Centro").performScrollTo().assertExists()
        compose.onNodeWithText("Shown 2 times").performScrollTo().assertExists()
        compose.onNodeWithText("Campaign 14").performScrollTo().assertExists()
        compose.onNodeWithText("The last 7 days, from 15 Nov 2026.").assertExists()
        compose.onNodeWithText("promotions applied cannot be totalled", substring = true).assertExists()
        compose.onNodeWithText("No customer is named.", substring = true).assertExists()
        compose.onNodeWithTag("review-promotions-cut").assertDoesNotExist()
    }

    @Test
    fun aCountCutAtThePageLimitSaysHowManyWereCounted() {
        show(
            promotions =
            PromotionsShownState(
                loading = false,
                rows = listOf(ShownPromotion(3, 12, null, 500)),
                since = LocalDate.parse("2026-11-15"),
                counted = 500,
                total = 1203
            )
        )
        compose.onNodeWithText("Counted 500 of 1203 records, the newest first.").performScrollTo().assertExists()
    }

    @Test
    fun promotionsThatAreEmptyLoadingOrRefusedSaySoToo() {
        show(promotions = PromotionsShownState(loading = false, since = LocalDate.parse("2026-11-15")))
        compose.onNodeWithTag("review-promotions-none").performScrollTo().assertExists()
    }

    @Test
    fun promotionsRefusedShowTheRefusal() {
        show(
            promotions = PromotionsShownState(
                loading = false,
                error = ApiError(403, "forbidden_role", "The authenticated role cannot perform this operation.", "c")
            )
        )
        compose.onNodeWithText("Retry").performScrollTo().assertExists()
    }
}
