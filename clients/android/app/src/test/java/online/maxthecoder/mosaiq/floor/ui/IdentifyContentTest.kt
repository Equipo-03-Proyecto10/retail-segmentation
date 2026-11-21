package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.net.FloorCustomer
import online.maxthecoder.mosaiq.floor.ui.identify.IdentifyBody
import online.maxthecoder.mosaiq.floor.ui.identify.ProfileBody
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class IdentifyContentTest {
    @get:Rule val compose = createComposeRule()

    private val searches = mutableListOf<Pair<String, Int>>()

    private fun show(content: @Composable () -> Unit) = compose.setContent {
        MosaiqTheme(
            darkTheme = false
        ) { content() }
    }

    private fun body(
        permission: CameraPermission,
        card: CardRead = CardRead.Idle,
        search: FloorSearchState = FloorSearchState()
    ) = show {
        IdentifyBody(
            permission = permission,
            scanning = false,
            cameraError = false,
            card = card,
            search = search,
            onScan = {},
            onGrantAccess = {},
            onOpenSettings = {},
            onSearch = { query, page -> searches += query to page },
            onOpenProfile = {},
            camera = {}
        )
    }

    @Test
    fun aDeniedCameraStillLeavesTheSearch() {
        body(CameraPermission.DENIED)
        compose.onNodeWithTag("camera-denied").assertIsDisplayed()

        compose.onNodeWithTag("floor-search-query").performScrollTo().performTextInput("Jo")
        compose.onNodeWithTag("floor-search-button").assertIsNotEnabled()
        compose.onNodeWithTag("floor-search-query").performTextInput("rdan")
        compose.onNodeWithTag("floor-search-button").performScrollTo().assertIsEnabled().performClick()

        assertEquals(listOf("Jordan" to 1), searches)
    }

    @Test
    fun anUnknownOrRevokedCardShowsOnlyAPlainMessage() {
        body(CameraPermission.GRANTED, CardRead.Revoked)
        compose.onNodeWithTag("card-revoked").assertIsDisplayed()
        compose.onNodeWithText("This card is no longer valid").assertIsDisplayed()
        // No refusal details: no code, no correlation id, no customer.
        compose.onAllNodesWithText("Details").assertCountEquals(0)
    }

    @Test
    fun resultsPageThroughTheSameQuery() {
        val jordan = FloorCustomer("7b3ad781-c5de-4f40-ae50-1fa825ce2b25", "Jordan Lee")
        body(
            CameraPermission.GRANTED,
            search = FloorSearchState(
                query = "Jordan",
                page = 1,
                totalPages = 3,
                customers = listOf(jordan),
                searched = true
            )
        )
        compose.onNodeWithText("Jordan Lee").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Next").performScrollTo().performClick()

        assertEquals(listOf("Jordan" to 2), searches)
    }

    @Test
    fun theProfileShowsOnlyTheElementsTheRoleReceived() {
        val seller = FloorCustomer("7b3ad781-c5de-4f40-ae50-1fa825ce2b25", "Jordan Lee")
        show { ProfileBody(FloorProfileState(seller.customerId, seller, byCard = true), onRetry = {}, onBack = {}) }

        compose.onNodeWithTag("floor-profile").assertIsDisplayed()
        compose.onNodeWithText("Identified by loyalty card").assertIsDisplayed()
        compose.onAllNodesWithText("Email").assertCountEquals(0)
        compose.onAllNodesWithText("Phone").assertCountEquals(0)
        compose.onAllNodesWithTag("floor-profile").assertCountEquals(1)
    }
}
