package online.maxthecoder.mosaiq.floor.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.AppText
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.CheckInResult
import online.maxthecoder.mosaiq.floor.net.Store
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.outbox.describe
import online.maxthecoder.mosaiq.floor.session.ChosenStore

/** The store picker's page. */
data class StoresState(
    val city: String = "",
    val page: Int = 1,
    val totalPages: Int = 1,
    val stores: List<Store> = emptyList(),
    val loading: Boolean = false,
    val error: ApiError? = null
)

/** How the location request went, said before the check-in is queued. */
enum class LocationMode { PRECISE, APPROXIMATE, DENIED }

/** What the check-in panel shows for the last check-in. */
sealed interface CheckInState {
    data object Idle : CheckInState

    data object Locating : CheckInState

    /** Queued; it is sent when the network allows, with its id fixed. */
    data class Queued(val rowId: Long, val note: String?) : CheckInState
}

@OptIn(ExperimentalCoroutinesApi::class)
class FloorViewModel(
    private val graph: AppGraph
) : ViewModel() {
    val stores = MutableStateFlow(StoresState())
    val chosen = MutableStateFlow(graph.session.chosenStore())
    val checkIn = MutableStateFlow<CheckInState>(CheckInState.Idle)

    /** The queued check-in, from Room, until the server's answer lands in it. */
    val lastCheckIn: StateFlow<OutboxRow?> =
        checkIn
            .flatMapLatest { state ->
                if (state is CheckInState.Queued) graph.outbox.observe(state.rowId) else emptyFlow()
            }.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** Refused rows and rows still waiting, for the outbox screen. */
    val open: StateFlow<List<OutboxRow>> =
        graph.outbox.observeOpen().stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    val userId: StateFlow<String?> = graph.session.userId

    fun searchStores(city: String = stores.value.city, page: Int = 1) {
        stores.value = stores.value.copy(city = city, page = page, loading = true, error = null)
        viewModelScope.launch {
            var failure: Exception? = null
            val answer =
                try {
                    graph.network.stores.listStores(active = true, city = city.trim().ifEmpty { null }, page = page)
                } catch (unreachable: IOException) {
                    null
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (unexpected: Exception) {
                    failure = unexpected
                    null
                }
            val body = answer?.body()
            stores.value =
                when {
                    failure != null -> stores.value.copy(loading = false, error = ApiError.unexpected())
                    answer == null -> stores.value.copy(loading = false, error = ApiError.unreachable(null))
                    !answer.isSuccessful || body == null ->
                        stores.value.copy(loading = false, error = ApiError.from(answer))
                    else ->
                        stores.value.copy(
                            loading = false,
                            stores = body.stores,
                            totalPages = maxOf(1, (body.pageInfo.total + body.pageInfo.size - 1) / body.pageInfo.size)
                        )
                }
        }
    }

    fun choose(store: Store) {
        val picked = ChosenStore(store.storeId, store.name)
        graph.session.choose(picked)
        chosen.value = picked
    }

    /**
     * Takes a fix when the permission allows it, then queues the check-in. The
     * check-in is recorded whatever the location says; the server decides.
     */
    fun checkInAt(store: ChosenStore, mode: LocationMode) {
        checkIn.value = CheckInState.Locating
        viewModelScope.launch {
            val fix = if (mode == LocationMode.DENIED) null else graph.location.currentFix()
            val note =
                when {
                    mode == LocationMode.DENIED ->
                        AppText.get(R.string.checkin_note_denied)
                    mode == LocationMode.APPROXIMATE ->
                        AppText.get(R.string.checkin_note_approximate)
                    fix == null -> AppText.get(R.string.checkin_note_no_fix)
                    else -> null
                }
            val rowId = graph.writes.queueCheckIn(store.storeId, fix)
            checkIn.value = CheckInState.Queued(rowId, note)
        }
    }

    fun dismiss(row: OutboxRow) {
        viewModelScope.launch { graph.outbox.dismiss(row.id) }
    }

    /** How many of the signed-in user's writes would wait if they signed out. */
    suspend fun pendingForCurrentUser(): Int = graph.session.userId.value?.let { graph.outbox.countPendingFor(it) } ?: 0

    companion object {
        /** Shown when the stored answer to a check-in cannot be read. */
        const val UNREADABLE_ANSWER = "Check-in sent. The answer could not be read."

        /** The server's answer to a check-in, in plain words, once it has arrived. */
        fun resultOf(row: OutboxRow?): String? {
            if (row?.state != RowState.DONE) return null
            val body = row.responseBody ?: return null
            val result =
                runCatching { json.decodeFromString(CheckInResult.serializer(), body) }.getOrNull()
                    ?: return UNREADABLE_ANSWER
            return describe(result)
        }
    }
}
