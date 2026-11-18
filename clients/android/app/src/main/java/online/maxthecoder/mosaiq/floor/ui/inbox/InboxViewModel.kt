package online.maxthecoder.mosaiq.floor.ui.inbox

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.alerts.AlertKind
import online.maxthecoder.mosaiq.floor.alerts.AlertTarget
import online.maxthecoder.mosaiq.floor.alerts.Inbox

/**
 * The updates screen and the top bar's badge (ADR-0071): the same pull the notifications come from, so a
 * user who declined notifications sees the same facts here.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class InboxViewModel(
    private val graph: AppGraph
) : ViewModel() {
    val inbox: StateFlow<Inbox> = graph.alerts.inbox

    /** Alerts not yet seen on the updates screen, for the badge. */
    val unread: StateFlow<Int> =
        graph.session.userId
            .flatMapLatest { id -> id?.let { graph.alerts.unread(it) } ?: flowOf(0) }
            .stateIn(viewModelScope, SharingStarted.Eagerly, 0)

    /** The writes this user had refused on this phone, as the outbox keeps them. */
    val refused: StateFlow<Int> =
        graph.session.userId
            .flatMapLatest { id -> id?.let { graph.outbox.observeRefusedCount(it) } ?: flowOf(0) }
            .stateIn(viewModelScope, SharingStarted.Eagerly, 0)

    /** What was unread when the screen opened, so it can still say "New" after marking it read. */
    val fresh = MutableStateFlow<Set<Pair<AlertKind, String>>>(emptySet())

    val refreshing = MutableStateFlow(false)

    /** A tapped notification, until the shell opens the screen for it. */
    val opened: StateFlow<AlertTarget?> = graph.alerts.opened

    val notificationsEnabled: Boolean get() = graph.alerts.notificationsEnabled

    fun consumeOpened() {
        graph.alerts.opened.value = null
    }

    /** The screen was shown: what was new is kept for it, then read. */
    fun seen() {
        viewModelScope.launch { fresh.value = fresh.value + graph.alerts.markRead() }
    }

    /** "Check now": the same poll the worker runs, at once. */
    fun refresh() {
        if (refreshing.value) return
        refreshing.value = true
        viewModelScope.launch {
            try {
                graph.alerts.poll()
                fresh.value = fresh.value + graph.alerts.markRead()
            } finally {
                refreshing.value = false
            }
        }
    }
}
