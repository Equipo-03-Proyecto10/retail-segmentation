package online.maxthecoder.mosaiq.floor

import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.outbox.asError
import org.junit.Assert.assertEquals
import org.junit.Test

/** The one mapping from a refused outbox row to the error the screens show. */
class OutboxRowErrorTest {
    private fun row(status: Int?, code: String?, message: String?, correlationId: String?) = OutboxRow(
        operation = Operation.SALE,
        path = "/p",
        body = "{}",
        clientId = "c",
        userId = "A",
        createdAt = 0,
        state = RowState.REFUSED,
        status = status,
        code = code,
        message = message,
        correlationId = correlationId
    )

    @Test
    fun aRowCarriesWhatTheServiceAnswered() {
        val error = row(409, "stock_insufficient", "Not enough stock.", "corr-1").asError()

        assertEquals(409, error.status)
        assertEquals("stock_insufficient", error.code)
        assertEquals("Not enough stock.", error.message)
        assertEquals("corr-1", error.correlationId)
    }

    @Test
    fun aRowWithNoAnswerSaysRefusedWithTheDefaultWords() {
        val error = row(null, null, null, null).asError()

        assertEquals(0, error.status)
        assertEquals("refused", error.code)
        assertEquals("The service refused this write.", error.message)
    }

    @Test
    fun aScreenCanGiveItsOwnWordsForAnEmptyMessage() {
        assertEquals("Sale refused.", row(422, "x", null, null).asError("Sale refused.").message)
        assertEquals("Own words.", row(422, "x", "Own words.", null).asError("Sale refused.").message)
    }
}
