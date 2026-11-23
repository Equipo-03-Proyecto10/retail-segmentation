package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.time.LocalDate
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.contractExample
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.DemandCoverageReport
import online.maxthecoder.mosaiq.floor.net.KpiDefinitions
import online.maxthecoder.mosaiq.floor.net.KpiReport
import online.maxthecoder.mosaiq.floor.net.PromotionPage
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.ui.indicators.IndicatorPeriod
import online.maxthecoder.mosaiq.floor.ui.indicators.Indicators
import online.maxthecoder.mosaiq.floor.ui.indicators.IndicatorsContent
import online.maxthecoder.mosaiq.floor.ui.indicators.IndicatorsState
import online.maxthecoder.mosaiq.floor.ui.indicators.ratioAsPercent
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** The store manager's screen (F27-08), drawn from the contracts' own examples. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class IndicatorsContentTest {
    @get:Rule val compose = createComposeRule()

    private inline fun <reified T> example(service: String, name: String): Answer<T> =
        Answer.Body(json.decodeFromString<T>(contractExample(service, name)))

    private val kpi = "analytics-kpis"

    private val indicators =
        Indicators(
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

    private fun show(state: IndicatorsState, onPeriod: (IndicatorPeriod) -> Unit = {}) {
        compose.setContent {
            MosaiqTheme {
                IndicatorsContent(state, IndicatorPeriod.LAST_30, "Coyoacan", onPeriod = onPeriod, onRetry = {})
            }
        }
    }

    @Test
    fun everyFigureOfTheStoreIsShown() {
        show(IndicatorsState.Shown(indicators))

        compose.onNodeWithText("Coyoacan").assertIsDisplayed()
        compose.onNodeWithText("$201,875.25").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Conversion rate").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Active customers").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Loyal customers 15 percent").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Footwear").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("120 on hand · 10.00 days of cover · restock 48").performScrollTo().assertIsDisplayed()
        compose.onAllNodesWithText("Synthetic")[0].performScrollTo().assertIsDisplayed()
    }

    @Test
    fun tappingAFigureShowsItsDefinitionFromTheCatalog() {
        show(IndicatorsState.Shown(indicators))

        compose.onNodeWithTag("figure-net-sales-net_amount").performScrollTo().performClick()

        compose.onNodeWithTag("definition").assertIsDisplayed()
        compose.onNodeWithText("Sum of net_amount over the lines in the window.", substring = true).assertIsDisplayed()
        compose.onNodeWithText("Close").performClick()
        assertEquals(0, compose.onAllNodesWithText("Close").fetchSemanticsNodes().size)
    }

    @Test
    fun aStockAlertShowsTheCalculationTheServiceStates() {
        show(IndicatorsState.Shown(indicators))

        compose.onNodeWithText("Footwear").performScrollTo().performClick()

        compose.onNodeWithText("replenishment_units = max(0", substring = true).assertIsDisplayed()
    }

    @Test
    fun anotherStoreIsRefusedInPlainWords() {
        show(IndicatorsState.StoreRefused(ApiError(403, "forbidden_store", "Outside your stores.", "corr-1")))

        compose.onNodeWithTag("store-refused").assertIsDisplayed()
        compose.onNodeWithText("This store is not assigned to you", substring = true).assertIsDisplayed()
    }

    @Test
    fun choosingAPeriodAsksForIt() {
        var chosen: IndicatorPeriod? = null
        show(IndicatorsState.Loading) { chosen = it }

        compose.onNodeWithTag("period-7").performClick()

        assertEquals(IndicatorPeriod.LAST_7, chosen)
    }

    @Test
    fun aRatioReadsAsAPercentage() {
        assertEquals("12.3", ratioAsPercent("0.1234"))
        assertEquals("n/a", ratioAsPercent("n/a"))
    }
}
