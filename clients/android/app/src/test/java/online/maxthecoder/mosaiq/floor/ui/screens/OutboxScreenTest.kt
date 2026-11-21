package online.maxthecoder.mosaiq.floor.ui.screens

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.height
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertHeightIsAtLeast
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewModelScope
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.time.Instant
import java.time.ZoneId
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import online.maxthecoder.mosaiq.floor.FloorTestApp
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxContent
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxList
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** The outbox of UX-11: badges, the synced empty state, counts as plurals, "Send now" and the refusal disclosure. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class OutboxScreenTest {
    @get:Rule val compose = createComposeRule()

    private val now = Instant.parse("2026-10-26T20:00:00Z")

    private fun row(
        id: Long,
        state: RowState,
        user: String = "A",
        code: String? = null,
        op: Operation = Operation.SALE
    ) = OutboxRow(
        id = id,
        operation = op,
        path = "/p",
        body = "{}",
        clientId = "client-$id",
        userId = user,
        createdAt = now.toEpochMilli() - 3_600_000,
        state = state,
        status = if (state == RowState.REFUSED) 409 else null,
        code = code,
        message = if (state == RowState.REFUSED) "Raw service message." else null,
        correlationId = if (state == RowState.REFUSED) "corr-77" else null
    )

    private fun outbox(rows: List<OutboxRow>?, sending: Set<Long> = emptySet(), onSendNow: (() -> Unit)? = null) =
        compose.setContent {
            MosaiqTheme(darkTheme = false) {
                Column {
                    Box(Modifier.height(900.dp)) {
                        OutboxContent(rows, "A", emptyMap(), {}, now, ZoneId.of("UTC"), sending, onSendNow)
                    }
                }
            }
        }

    @Test
    fun beforeTheQueueIsReadItSaysSoInsteadOfClaimingEverythingIsSynced() {
        outbox(null)
        compose.onNodeWithText("Reading the writes saved on this phone…").assertExists()
        compose.onNodeWithText("Everything is synced").assertDoesNotExist()
        compose.onNodeWithText("Send now").assertDoesNotExist()
    }

    @Test
    fun theOutboxListLoadsFromTheQueueAndThenShowsItsRows() {
        val app = ApplicationProvider.getApplicationContext<FloorTestApp>()
        val graph = app.build("http://127.0.0.1:9/")
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
        val model = FloorViewModel(graph)
        try {
            compose.setContent { MosaiqTheme(darkTheme = false) { OutboxList(model) } }
            compose.waitUntil(TIMEOUT_MS) {
                compose.onAllNodesWithText("Everything is synced").fetchSemanticsNodes().isNotEmpty()
            }
            runBlocking { graph.writes.queueCheckIn(3, null) }
            compose.waitUntil(TIMEOUT_MS) {
                compose.onAllNodesWithText("Check-in").fetchSemanticsNodes().isNotEmpty()
            }
            compose.onNodeWithText("Everything is synced").assertDoesNotExist()
        } finally {
            model.viewModelScope.cancel()
            app.database.close()
        }
    }

    @Test
    fun anEmptyQueueSaysEverythingIsSynced() {
        outbox(emptyList())
        compose.onNodeWithText("Everything is synced").assertExists()
        compose.onNodeWithText("Send now").assertDoesNotExist()
    }

    @Test
    fun everyStatusHasItsOwnBadgeAndOnlyTheRowOnTheWireIsSending() {
        outbox(
            listOf(
                row(1, RowState.PENDING),
                row(2, RowState.PENDING),
                row(3, RowState.DONE),
                row(4, RowState.REFUSED, code = "validation_failed")
            ),
            sending = setOf(2L)
        )
        compose.onAllNodesWithText("Waiting to send").assertCountEquals(1)
        compose.onAllNodesWithText("Sending").assertCountEquals(1)
        compose.onAllNodesWithText("Sent").assertCountEquals(1)
        compose.onAllNodesWithText("Needs attention").assertCountEquals(1)
    }

    @Test
    fun countsUseTheRightPluralForm() {
        outbox(listOf(row(1, RowState.PENDING), row(2, RowState.REFUSED)))
        compose.onNodeWithText("1 write waiting to send · 1 needs attention").assertExists()
    }

    @Test
    fun countsUseThePluralOtherForm() {
        outbox(
            listOf(
                row(1, RowState.PENDING),
                row(2, RowState.PENDING),
                row(3, RowState.REFUSED),
                row(4, RowState.REFUSED)
            )
        )
        compose.onNodeWithText("2 writes waiting to send · 2 need attention").assertExists()
    }

    @Test
    fun sendNowIsOfferedForMyWaitingWritesAndAsksTheQueueToSend() {
        var asked = 0
        outbox(listOf(row(1, RowState.PENDING)), onSendNow = { asked++ })
        compose.onNodeWithText("Send now").assertHeightIsAtLeast(Spacing.MinTouchTarget).performClick()
        assertEquals(1, asked)
    }

    @Test
    fun sendNowIsNotOfferedWhenNothingOfMineIsWaiting() {
        outbox(
            listOf(row(1, RowState.PENDING, user = "B"), row(2, RowState.DONE), row(3, RowState.REFUSED)),
            onSendNow = {}
        )
        compose.onNodeWithText("Send now").assertDoesNotExist()
    }

    @Test
    fun aRefusedWriteShowsItsReasonAndKeepsTheTechnicalDetailsBehindDetails() {
        outbox(listOf(row(1, RowState.REFUSED, code = "validation_failed")))
        compose.onNodeWithText("Raw service message.").assertExists()
        compose.onNodeWithText("Code: validation_failed").assertDoesNotExist()
        compose.onNodeWithText("Correlation ID: corr-77").assertDoesNotExist()

        compose.onNodeWithText("Details").performScrollTo().performClick()

        compose.onNodeWithText("Code: validation_failed").assertExists()
        compose.onNodeWithText("Correlation ID: corr-77").assertExists()
    }

    @Test
    fun aRefusalByRoleOrStoreIsPutInPlainWords() {
        outbox(
            listOf(
                row(1, RowState.REFUSED, code = "forbidden_role"),
                row(2, RowState.REFUSED, code = "forbidden_store")
            )
        )
        compose.onNodeWithText("Your role is not allowed", substring = true).assertExists()
        compose.onNodeWithText("not assigned to you", substring = true).assertExists()
        compose.onAllNodesWithText("Raw service message.").assertCountEquals(0)
    }

    private companion object {
        const val TIMEOUT_MS = 5_000L
    }
}
