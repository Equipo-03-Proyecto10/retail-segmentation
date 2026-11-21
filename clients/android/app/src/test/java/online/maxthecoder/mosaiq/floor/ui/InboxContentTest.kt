package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.runtime.saveable.SaverScope
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.alerts.AlertKind
import online.maxthecoder.mosaiq.floor.alerts.CampaignAlert
import online.maxthecoder.mosaiq.floor.alerts.Inbox
import online.maxthecoder.mosaiq.floor.net.CaptureIncident
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.ui.inbox.InboxContent
import online.maxthecoder.mosaiq.floor.ui.inbox.focusOf
import online.maxthecoder.mosaiq.floor.ui.shell.FloorLocation
import online.maxthecoder.mosaiq.floor.ui.shell.FloorLocationSaver
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** The updates screen: with notifications declined it shows the same facts, and a tap opens its subject. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class InboxContentTest {
    @get:Rule val compose = createComposeRule()

    private fun promotion(id: Int, campaignId: Int, name: String) =
        Promotion(id, campaignId, name, "15.00", "2026-11-01", "2026-11-30", listOf(3), listOf(1), true)

    private val inbox =
        Inbox(
            campaigns =
            listOf(
                CampaignAlert(12, promotion(38, 12, "Loyal customers"), endingSoon = false),
                CampaignAlert(14, promotion(41, 14, "Weekend basket"), endingSoon = true)
            ),
            incidents =
            listOf(
                CaptureIncident(
                    "i-1",
                    "SALE",
                    3,
                    409,
                    "stock_unavailable",
                    "2026-11-05T14:02:10Z",
                    "2026-11-05T14:02:41Z",
                    "OPEN"
                )
            ),
            readsIncidents = true,
            polledAt = java.time.Instant.EPOCH
        )

    @Test
    fun withNotificationsDeclinedEveryFactIsOnTheScreen() {
        var outbox = 0
        compose.setContent {
            MosaiqTheme {
                InboxContent(
                    inbox = inbox,
                    fresh = setOf(AlertKind.CAMPAIGN_STARTED to "14", AlertKind.CAPTURE_INCIDENT to "i-1"),
                    refused = 2,
                    focus = null,
                    refreshing = false,
                    notificationsOn = false,
                    onRefresh = {},
                    onOpenOutbox = { outbox++ }
                )
            }
        }
        compose.onNodeWithTag("inbox-notifications-off").assertIsDisplayed()
        compose.onNodeWithText("2 items queued on this phone were refused").assertIsDisplayed()
        compose.onNodeWithText("Store 3 · Sale refused").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Weekend basket · 15% off").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Ends soon").performScrollTo().assertIsDisplayed()
        compose.onNodeWithTag("inbox-open-outbox").performScrollTo().performClick()
        assertEquals(1, outbox)
    }

    @Test
    fun aTappedCampaignNotificationOpensThatPromotionFirst() {
        val focus = focusOf(AlertKind.CAMPAIGN_STARTED, "14")
        compose.setContent {
            MosaiqTheme {
                InboxContent(inbox, emptySet(), 0, focus, false, true, {}, {})
            }
        }
        compose.onNodeWithTag("inbox-focused").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Weekend basket · 15% off").assertIsDisplayed()
    }

    @Test
    fun theUpdatesLocationSurvivesARestoreWithItsFocus() {
        val scope = SaverScope { true }
        listOf(FloorLocation.Inbox("campaign:14"), FloorLocation.Inbox()).forEach { location ->
            val saved = with(FloorLocationSaver) { scope.save(location) }!!
            assertEquals(location, FloorLocationSaver.restore(saved))
        }
        assertEquals(FloorLocation.Outbox, FloorLocation.Inbox().back)
    }
}
