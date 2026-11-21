package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsNotSelected
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.net.Channel
import online.maxthecoder.mosaiq.floor.net.ConsentState
import online.maxthecoder.mosaiq.floor.net.FloorCustomer
import online.maxthecoder.mosaiq.floor.net.PreferenceProfile
import online.maxthecoder.mosaiq.floor.net.PurposeConsent
import online.maxthecoder.mosaiq.floor.ui.record.EnrolFields
import online.maxthecoder.mosaiq.floor.ui.record.NewCustomerBody
import online.maxthecoder.mosaiq.floor.ui.record.RecordActions
import online.maxthecoder.mosaiq.floor.ui.record.RecordBody
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class CustomerRecordContentTest {
    @get:Rule val compose = createComposeRule()

    private fun show(content: @Composable () -> Unit) = compose.setContent {
        MosaiqTheme(
            darkTheme = false
        ) { content() }
    }

    @Test
    fun theNewCustomerFormChoosesNoConsentAndWaitsForEveryAnswer() {
        show {
            NewCustomerBody(
                fields = EnrolFields(name = "Ana Torres"),
                onFields = {},
                state = EnrolState(channels = listOf(Channel(3, "physical_store")), selectedChannelId = 3),
                onLoadChannels = {},
                onChooseChannel = {},
                onAnswer = { _, _ -> },
                onSubmit = {}
            )
        }
        CONSENT_PURPOSES.forEach { purpose ->
            listOf(GRANTED, WITHDRAWN).forEach {
                compose.onNodeWithTag("consent-$purpose-$it").performScrollTo().assertIsNotSelected()
            }
        }
        compose.onNodeWithTag("enrol-submit").performScrollTo().assertIsNotEnabled()
    }

    @Test
    fun theRecordShowsConsentBenefitsAndPreferencesWithWhoMayChangeEach() {
        val consent =
            ConsentState(
                "c",
                "2026-11-05T14:30:00Z",
                CONSENT_PURPOSES.map { PurposeConsent(it, "NOT_RECORDED") }
            )
        show {
            RecordBody(
                state =
                RecordState(
                    customerId = "c",
                    customer = FloorCustomer("c", "Jordan Lee"),
                    consent = consent,
                    profile = PreferenceProfile("c", listOf(3), emptyList()),
                    channels = listOf(Channel(3, "physical_store"))
                ),
                history = HistoryState(),
                userId = "A",
                actions = RecordActions()
            )
        }
        compose.onNodeWithTag("who-CONSENT").performScrollTo()
            .assertTextContains("Loyalty officer, Administrator", substring = true)
        compose.onNodeWithTag("who-PREFERRED_CHANNELS").performScrollTo()
            .assertTextContains("Loyalty officer, Store manager, Administrator", substring = true)
        compose.onNodeWithTag("who-INTERESTS").performScrollTo()
        compose.onNodeWithTag("record-benefits").performScrollTo()
        // A purpose never recorded is shown as not asked, and no answer is chosen for it.
        compose.onNodeWithTag("consent-PROFILING-GRANTED").performScrollTo().assertIsNotSelected()
        compose.onNodeWithText("Not asked yet", substring = false, useUnmergedTree = true)
        compose.onNodeWithTag("record-consent-save").performScrollTo().assertIsNotEnabled()
    }
}
