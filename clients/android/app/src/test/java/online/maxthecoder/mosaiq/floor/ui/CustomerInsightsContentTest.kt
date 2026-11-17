package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.CurrentAssignment
import online.maxthecoder.mosaiq.floor.net.RecommendationItem
import online.maxthecoder.mosaiq.floor.net.RecommendationResult
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class CustomerInsightsContentTest {
    @get:Rule val compose = createComposeRule()

    private val assigned = CurrentAssignment("c1", "assigned", "loyal_regulars", 4, 3, 5, "2026-03-12T09:00:00Z")
    private val shown =
        ShownRecommendation(
            1,
            RecommendationItem(9, 2, 6, emptyList()),
            "Trail socks",
            listOf("3 other customers in the same segment bought it")
        )

    private fun show(state: InsightsState, onRetry: () -> Unit = {}, onOpen: (ShownRecommendation) -> Unit = {}) =
        compose.setContent {
            MosaiqTheme(darkTheme = false) { CustomerInsightsContent(state, onRetry, onOpen) }
        }

    @Test
    fun loadingShowsAProgressLabelAndNoRecommendations() {
        show(InsightsState(customerId = "c1", loading = true))
        compose.onNodeWithText("Loading the segment and recommendations…").assertExists()
        compose.onNodeWithText("Recommendations").assertDoesNotExist()
    }

    @Test
    fun anAssignedSegmentShowsABadgeLabelAndExplanation() {
        show(InsightsState(customerId = "c1", assignment = assigned))
        compose.onNodeWithTag("segment").assertExists()
        compose.onNodeWithText("Loyal regulars").assertExists()
        compose.onNodeWithText(
            "Scores: recency 4, frequency 3, spend 5. In this segment since 12 Mar 2026."
        ).assertExists()
    }

    @Test
    fun theSameSegmentAlwaysGetsTheSameLabelAndNoSegmentIsAnEmptyState() {
        assertEquals("Loyal regulars", InsightsState(assignment = assigned).segmentLabel())
        show(InsightsState(customerId = "c1", assignment = CurrentAssignment("c1", "no_open_assignment")))
        compose.onNodeWithText("No segment yet for this customer").assertExists()
        compose.onNodeWithText("No segmentation run has assigned this customer yet.").assertExists()
    }

    @Test
    fun anErrorOffersRetry() {
        var retries = 0
        show(
            InsightsState(customerId = "c1", assignmentError = ApiError(503, "down", "Service down", null)),
            onRetry = { retries++ }
        )
        compose.onNodeWithText("Service down").assertExists()
        compose.onNodeWithText("Retry").performClick()
        assertEquals(1, retries)
    }

    @Test
    fun aRecommendationIsOneButtonThatOpensTheProductOnce() {
        val opened = mutableListOf<Int>()
        show(
            InsightsState(customerId = "c1", assignment = assigned, items = listOf(shown)),
            onOpen = { opened += it.item.productId }
        )
        compose.onNodeWithContentDescription("Open Trail socks in Scan").assertHasClickAction()
        compose.onNodeWithTag("recommendation-1").performClick()
        assertEquals(listOf(9), opened)
    }

    @Test
    fun noRecommendationsSaysWhy() {
        show(
            InsightsState(
                customerId = "c1",
                assignment = assigned,
                recommendations = RecommendationResult("c1", "none_match")
            )
        )
        compose.onNodeWithTag("why-none").assertExists()
    }
}
