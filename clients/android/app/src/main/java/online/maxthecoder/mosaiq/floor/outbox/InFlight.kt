package online.maxthecoder.mosaiq.floor.outbox

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

/**
 * The rows whose request is on the wire right now, so the outbox can say "Sending" for exactly those.
 * Kept in memory only: the stored [RowState] is unchanged (#433), and a restart starts with none in flight.
 */
class InFlight {
    private val current = MutableStateFlow<Set<Long>>(emptySet())

    val ids: StateFlow<Set<Long>> = current

    /** Marks [id] in flight for the duration of [block], however it ends. */
    inline fun <T> during(id: Long, block: () -> T): T {
        begin(id)
        try {
            return block()
        } finally {
            end(id)
        }
    }

    fun begin(id: Long) = current.update { it + id }

    fun end(id: Long) = current.update { it - id }
}
