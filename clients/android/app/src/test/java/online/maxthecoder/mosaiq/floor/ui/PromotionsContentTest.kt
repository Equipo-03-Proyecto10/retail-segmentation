package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class PromotionsContentTest {
    @get:Rule val compose = createComposeRule()

    private val offer =
        Promotion(38, 12, "Loyal customers", "15.00", "2026-11-01", "2026-11-30", listOf(3), listOf(3), true)

    private fun show(state: PromotionsState, onRecord: (Promotion) -> Unit = {}) = compose.setContent {
        MosaiqTheme(darkTheme = false) { PromotionsContent(state, onRetry = {}, onRecord = onRecord) }
    }

    @Test
    fun eachPromotionShowsItsDiscountAndValidityAndRecordsOnlyOnTheSellersTap() {
        val recorded = mutableListOf<Promotion>()
        show(PromotionsState(customerId = "c1", storeId = 3, promotions = listOf(offer))) { recorded += it }

        compose.onNodeWithText("Loyal customers · 15% off").assertExists()
        compose.onNodeWithText("Valid from 1 Nov 2026 to 30 Nov 2026").assertExists()
        assertEquals(emptyList<Promotion>(), recorded)
        compose.onNodeWithTag("record-38").performClick()
        assertEquals(listOf(offer), recorded)
    }

    @Test
    fun aRecordedPromotionSaysSoAndOffersNoSecondTap() {
        show(PromotionsState(customerId = "c1", storeId = 3, promotions = listOf(offer), recorded = setOf(38)))
        compose.onNodeWithText("Shown and recorded").assertExists()
        compose.onNodeWithTag("record-38").assertDoesNotExist()
    }

    @Test
    fun noPromotionIsSaidInPlainWords() {
        show(PromotionsState(customerId = "c1", storeId = 3))
        compose.onNodeWithTag("promotions-none").assertExists()
    }

    @Test
    fun anUnknownArmIsExplainedAndNotCalledEmpty() {
        val down = ApiError(503, "operation_unavailable", "Unavailable.", "corr-1")
        show(PromotionsState(customerId = "c1", storeId = 3, armError = down))
        compose.onNodeWithText(
            "Some promotions are held back: the experiment service did not say whether this customer may see them."
        ).assertExists()
        compose.onNodeWithTag("promotions-none").assertDoesNotExist()
    }

    @Test
    fun withoutACustomerNothingIsShown() {
        show(PromotionsState())
        compose.onNodeWithTag("promotions").assertDoesNotExist()
    }
}
