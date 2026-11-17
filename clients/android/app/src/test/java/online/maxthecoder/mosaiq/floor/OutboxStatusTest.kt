package online.maxthecoder.mosaiq.floor

import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxStatus
import online.maxthecoder.mosaiq.floor.ui.outbox.outboxStatus
import org.junit.Assert.assertEquals
import org.junit.Test

/** The outbox badge each stored row state maps to. */
class OutboxStatusTest {
    @Test
    fun pendingWaitsUnlessInFlight() {
        assertEquals(OutboxStatus.WAITING, outboxStatus(RowState.PENDING))
        assertEquals(OutboxStatus.SENDING, outboxStatus(RowState.PENDING, sending = true))
    }

    @Test
    fun settledStatesIgnoreInFlight() {
        assertEquals(OutboxStatus.SENT, outboxStatus(RowState.DONE, sending = true))
        assertEquals(OutboxStatus.NEEDS_ATTENTION, outboxStatus(RowState.REFUSED))
    }
}
