package online.maxthecoder.mosaiq.floor.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertHeightIsAtLeast
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertWidthIsAtLeast
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasStateDescription
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.ErrorDetail
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class ComponentsTest {
    @get:Rule val compose = createComposeRule()

    private fun show(content: @Composable () -> Unit) =
        compose.setContent { MosaiqTheme(darkTheme = false) { Column { content() } } }

    @Test
    fun buttonClicksAndIsAtLeastATouchTarget() {
        var clicks = 0
        show { MqButton("Save", { clicks++ }) }
        compose.onNodeWithText(
            "Save"
        ).assertHeightIsAtLeast(Spacing.MinTouchTarget).assertWidthIsAtLeast(Spacing.MinTouchTarget)
        compose.onNodeWithText("Save").assertHeightIsAtLeast(48.dp).performClick()
        assertEquals(1, clicks)
    }

    @Test
    fun everyVariantRendersItsLabelAndHonorsDisabled() {
        var clicks = 0
        show {
            MqButtonVariant.entries.forEach {
                MqButton(it.name, { clicks++ }, variant = it, enabled = false)
            }
        }
        MqButtonVariant.entries.forEach {
            compose.onNodeWithText(it.name).assertIsNotEnabled().performClick()
        }
        assertEquals(0, clicks)
    }

    @Test
    fun loadingButtonIgnoresTapsAndSaysSo() {
        var clicks = 0
        show { MqButton("Saving", { clicks++ }, loading = true, leadingIcon = R.drawable.ic_lucide_plus) }
        compose.onNodeWithText("Saving").assertIsNotEnabled().performClick()
        assertEquals(0, clicks)
        compose.onNode(hasStateDescription("Loading")).assertExists()
    }

    @Test
    fun fullWidthButtonIsWiderThanItsLabel() {
        show { MqButton("Go", {}, fullWidth = true) }
        compose.onNodeWithText("Go").assertWidthIsAtLeast(200.dp)
    }

    @Test
    fun textFieldShowsLabelSupportingAndErrorText() {
        show {
            MqTextField("", {}, label = "Email", supportingText = "Work address")
            MqTextField("", {}, label = "City", errorText = "Enter a city.")
        }
        compose.onNodeWithText("Email").assertExists()
        compose.onNodeWithText("Work address").assertExists()
        compose.onNodeWithText("Enter a city.").assertExists()
    }

    @Test
    fun errorTextFieldReplacesSupportingText() {
        show { MqTextField("", {}, label = "City", supportingText = "Help", errorText = "Enter a city.") }
        compose.onNodeWithText("Enter a city.").assertExists()
        compose.onNodeWithText("Help").assertDoesNotExist()
    }

    @Test
    fun textFieldIsAtLeast48dpTallAndTakesInput() {
        var text by mutableStateOf("")
        show { MqTextField(text, { text = it }, label = "City") }
        compose.onNode(hasSetTextAction()).assertHeightIsAtLeast(48.dp).performTextInput("Lima")
        assertEquals("Lima", text)
    }

    @Test
    fun textFieldRunsItsImeAction() {
        var done = 0
        show {
            MqTextField(
                "",
                {},
                label = "City",
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { done++ })
            )
        }
        compose.onNode(hasSetTextAction()).performClick()
        compose.onNode(hasSetTextAction()).performImeAction()
        assertEquals(1, done)
    }

    @Test
    fun passwordFieldTogglesVisibility() {
        show { MqTextField("secret", {}, label = "Password", password = true) }
        compose.onNodeWithContentDescription(
            "Show password"
        ).assertHeightIsAtLeast(Spacing.MinTouchTarget).performClick()
        compose.onNodeWithContentDescription("Hide password").performClick()
        compose.onNodeWithContentDescription("Show password").assertExists()
    }

    @Test
    fun listRowIsTallEnoughAndWholeRowClicks() {
        var clicks = 0
        show { MqListRow("Calle Norte", supporting = "Madrid", onClick = { clicks++ }, chevron = true) }
        compose.onNode(hasClickAction()).assertHeightIsAtLeast(56.dp)
        compose.onNodeWithText("Madrid").performClick()
        assertEquals(1, clicks)
    }

    @Test
    fun plainListRowHasNoClickAction() {
        show { MqListRow("Static", trailing = { Text("x") }) }
        compose.onNode(hasClickAction()).assertDoesNotExist()
    }

    @Test
    fun badgeAlwaysCarriesItsText() {
        show {
            MqTone.entries.forEach { MqBadge("Tone ${it.name}", tone = it, leadingIcon = R.drawable.ic_lucide_info) }
        }
        MqTone.entries.forEach { compose.onNodeWithText("Tone ${it.name}").assertExists() }
    }

    @Test
    fun loadingShowsItsLabelInlineAndFillingArea() {
        show {
            MqLoading("Loading stores")
            MqLoading("Loading sales", fillArea = true)
        }
        compose.onNodeWithText("Loading stores").assertExists()
        compose.onNodeWithText("Loading sales").assertExists()
    }

    @Test
    fun emptyStateShowsTextAndRunsItsAction() {
        var clicks = 0
        show {
            MqEmptyState(
                R.drawable.ic_lucide_package,
                "Everything is sent",
                message = "Nothing is waiting.",
                actionLabel = "Refresh",
                onAction = { clicks++ }
            )
        }
        compose.onNodeWithText("Everything is sent").assertExists()
        compose.onNodeWithText("Nothing is waiting.").assertExists()
        compose.onNodeWithText("Refresh").assertHeightIsAtLeast(Spacing.MinTouchTarget).performClick()
        assertEquals(1, clicks)
    }

    @Test
    fun emptyStateWithoutActionHasNoButton() {
        show { MqEmptyState(R.drawable.ic_lucide_package, "Nothing here") }
        compose.onNode(hasClickAction()).assertDoesNotExist()
    }

    @Test
    fun pagerShowsPositionAndDisablesTheEnds() {
        var previous = 0
        var next = 0
        show { MqPager(page = 1, pageCount = 3, onPrevious = { previous++ }, onNext = { next++ }) }
        compose.onNodeWithText("Page 1 of 3").assertExists()
        compose.onNodeWithText("Previous").assertIsNotEnabled()
        compose.onNodeWithText("Next").assertIsEnabled().assertHeightIsAtLeast(48.dp).performClick()
        assertEquals(0, previous)
        assertEquals(1, next)
    }

    @Test
    fun pagerLastPageDisablesNext() {
        show { MqPager(page = 3, pageCount = 3, onPrevious = {}, onNext = {}) }
        compose.onNodeWithText("Next").assertIsNotEnabled()
        compose.onNodeWithText("Previous").assertIsEnabled()
    }

    @Test
    fun loadMoreClicksAndLoadingBlocksIt() {
        var clicks = 0
        var loading by mutableStateOf(false)
        show { MqLoadMore(onClick = { clicks++ }, loading = loading) }
        compose.onNodeWithText("Load more").assertHeightIsAtLeast(48.dp).performClick()
        assertEquals(1, clicks)
        loading = true
        compose.waitForIdle()
        compose.onNodeWithText("Load more").assertIsNotEnabled()
    }

    @Test
    fun errorPanelShowsPlainMessageAndHidesDetailsUntilExpanded() {
        val error =
            ApiError(
                409,
                "stale_version",
                "That record changed. Reload and try again.",
                "corr-42",
                listOf(ErrorDetail("quantity", "too_high"))
            )
        show { MqErrorPanel(error) }
        compose.onNodeWithText("That record changed. Reload and try again.").assertExists()
        compose.onNodeWithText("Code: stale_version").assertDoesNotExist()
        compose.onNodeWithText("Correlation ID: corr-42").assertDoesNotExist()
        compose.onNodeWithText("Details").assertExists()
        compose.onNode(hasClickAction() and hasText("Details")).assertHeightIsAtLeast(48.dp).performClick()
        compose.onNodeWithText("Code: stale_version").assertExists()
        compose.onNodeWithText("quantity: too_high").assertExists()
        compose.onNodeWithText("Correlation ID: corr-42").assertExists()
    }

    @Test
    fun errorPanelFriendlyOverridesMessageAndRetryRuns() {
        var retries = 0
        show {
            MqErrorPanel(
                ApiError.unreachable(null),
                onRetry = { retries++ },
                friendly = "No connection. Your changes are saved and will be sent when you are back online."
            )
        }
        compose.onNodeWithText(
            "No connection. Your changes are saved and will be sent when you are back online."
        ).assertExists()
        compose.onNodeWithText(ApiError.unreachable(null).message).assertDoesNotExist()
        compose.onNodeWithText("Retry").assertHeightIsAtLeast(48.dp).performClick()
        assertEquals(1, retries)
    }

    @Test
    fun errorPanelWithoutRetryHasNoRetryButton() {
        show { MqErrorPanel(ApiError.unexpected()) }
        compose.onNodeWithText("Something went wrong. Try again.").assertExists()
        compose.onNodeWithText("Retry").assertDoesNotExist()
    }

    @Test
    fun cardAndSectionShowTheirContent() {
        show {
            MqCard { Text("In a card") }
            MqSection("Customer") { Text("Ana") }
        }
        compose.onNodeWithText("In a card").assertExists()
        compose.onNodeWithText("Customer").assertExists()
        compose.onNodeWithText("Ana").assertExists()
    }

    @Test
    fun stepperCallsBackAndHasBigTargets() {
        var down = 0
        var up = 0
        show { MqQuantityStepper(value = 3, onDecrease = { down++ }, onIncrease = { up++ }) }
        compose.onNodeWithText("3").assertExists()
        compose.onNodeWithContentDescription(
            "Decrease quantity"
        ).assertHeightIsAtLeast(48.dp).assertWidthIsAtLeast(48.dp).performClick()
        compose.onNodeWithContentDescription(
            "Increase quantity"
        ).assertHeightIsAtLeast(48.dp).assertWidthIsAtLeast(48.dp).performClick()
        assertEquals(1, down)
        assertEquals(1, up)
    }

    @Test
    fun stepperStopsAtItsLimits() {
        show { MqQuantityStepper(value = 1, onDecrease = {}, onIncrease = {}, min = 1, max = 1) }
        compose.onNodeWithContentDescription("Decrease quantity").assertIsNotEnabled()
        compose.onNodeWithContentDescription("Increase quantity").assertIsNotEnabled()
    }
}
