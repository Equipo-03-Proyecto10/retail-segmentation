package online.maxthecoder.mosaiq.floor.ui.outbox

import online.maxthecoder.mosaiq.floor.outbox.RowState

/** What the associate sees for a queued write; the stored [RowState] is unchanged (#433). */
enum class OutboxStatus { WAITING, SENDING, SENT, NEEDS_ATTENTION }

/** [sending] is true only while the worker has this row's request in flight. */
fun outboxStatus(state: RowState, sending: Boolean = false): OutboxStatus = when (state) {
    RowState.PENDING -> if (sending) OutboxStatus.SENDING else OutboxStatus.WAITING
    RowState.DONE -> OutboxStatus.SENT
    RowState.REFUSED -> OutboxStatus.NEEDS_ATTENTION
}
