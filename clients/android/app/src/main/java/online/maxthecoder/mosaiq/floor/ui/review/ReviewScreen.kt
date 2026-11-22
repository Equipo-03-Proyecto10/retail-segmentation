package online.maxthecoder.mosaiq.floor.ui.review

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.time.Instant
import java.time.ZoneId
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.CaptureIncident
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqCard
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqSection
import online.maxthecoder.mosaiq.floor.ui.components.MqTextField
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.formatDate
import online.maxthecoder.mosaiq.floor.ui.humanize
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxItem
import online.maxthecoder.mosaiq.floor.ui.outbox.OutboxSummary
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * What went wrong on the floor (F27-14): the capture incidents of the supervisor's stores, this phone's pending
 * and refused syncs, and the promotions shown by store. The syncs are the outbox's own rows, so the state
 * and the reason read as they do there.
 */
@Composable
fun ReviewScreen(model: ReviewViewModel, floor: FloorViewModel) {
    LaunchedEffect(model) { model.load() }
    val incidents by model.incidents.collectAsStateWithLifecycle()
    val promotions by model.promotions.collectAsStateWithLifecycle()
    val rows by floor.open.collectAsStateWithLifecycle()
    val userId by floor.userId.collectAsStateWithLifecycle()
    val sending by floor.sending.collectAsStateWithLifecycle()
    ReviewContent(
        incidents = incidents,
        promotions = promotions,
        syncs = rows,
        currentUserId = userId,
        sending = sending,
        onReview = model::review,
        onRetryIncidents = model::loadIncidents,
        onRetryPromotions = model::loadPromotions,
        onDismiss = floor::dismiss,
        onSendNow = floor::sendNow
    )
}

/** Stateless, so previews and tests drive every state. [syncs] is null until the queue has been read once. */
@Composable
fun ReviewContent(
    incidents: IncidentsState,
    promotions: PromotionsShownState,
    syncs: List<OutboxRow>?,
    currentUserId: String?,
    sending: Set<Long>,
    onReview: (CaptureIncident, String, String) -> Unit,
    onRetryIncidents: () -> Unit,
    onRetryPromotions: () -> Unit,
    onDismiss: (OutboxRow) -> Unit,
    onSendNow: () -> Unit,
    now: Instant = Instant.now(),
    zone: ZoneId = ZoneId.systemDefault()
) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).testTag("review"),
        verticalArrangement = Arrangement.spacedBy(Spacing.Xl)
    ) {
        Text(
            stringResource(R.string.review_title),
            style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.semantics { heading() }
        )
        IncidentsPart(incidents, promotions.storeNames, onReview, onRetryIncidents)
        SyncsPart(syncs, currentUserId, sending, promotions.storeNames, onDismiss, onSendNow, now, zone)
        PromotionsPart(promotions, onRetryPromotions)
    }
}

@Composable
private fun IncidentsPart(
    state: IncidentsState,
    storeNames: Map<Int, String>,
    onReview: (CaptureIncident, String, String) -> Unit,
    onRetry: () -> Unit
) {
    MqSection(stringResource(R.string.review_incidents_title), Modifier.testTag("review-incidents")) {
        val error = state.error
        when {
            state.loading && state.incidents.isEmpty() -> MqLoading(stringResource(R.string.review_incidents_loading))
            error != null -> MqErrorPanel(error, onRetry = onRetry)
            state.incidents.isEmpty() ->
                Text(
                    stringResource(R.string.review_incidents_none),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("review-incidents-none")
                )
        }
        listOf(OPEN, ACKNOWLEDGED, RESOLVED).forEach { status ->
            val group = state.withStatus(status)
            if (group.isNotEmpty()) {
                Text(
                    stringResource(R.string.review_group, stateLabel(status), group.size),
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.semantics { heading() }.testTag("review-group-$status")
                )
                group.forEach { incident ->
                    IncidentCard(
                        incident,
                        storeNames,
                        busy = incident.incidentId in state.busy,
                        error = state.reviewErrors[incident.incidentId],
                        onReview = onReview
                    )
                }
            }
        }
        if (state.total > state.incidents.size && state.incidents.isNotEmpty()) {
            Text(
                stringResource(R.string.review_incidents_more, state.incidents.size, state.total),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        if (!state.loading) {
            MqButton(
                stringResource(R.string.review_refresh),
                onRetry,
                variant = MqButtonVariant.Quiet,
                leadingIcon = R.drawable.ic_lucide_refresh_cw,
                modifier = Modifier.testTag("review-refresh")
            )
        }
    }
}

@Composable
private fun stateLabel(status: String): String = when (status) {
    OPEN -> stringResource(R.string.review_state_open)
    ACKNOWLEDGED -> stringResource(R.string.review_state_acknowledged)
    RESOLVED -> stringResource(R.string.review_state_resolved)
    else -> humanize(status)
}

/** An incident as reported: its store, what was refused and why, and its reviews. Nobody is named. */
@Composable
private fun IncidentCard(
    incident: CaptureIncident,
    storeNames: Map<Int, String>,
    busy: Boolean,
    error: ApiError?,
    onReview: (CaptureIncident, String, String) -> Unit
) {
    // A note belongs to the state it was typed for; it survives rotation and a restart of the process.
    var note by rememberSaveable(incident.incidentId, incident.status) { mutableStateOf("") }
    val store = storeNames[incident.storeId] ?: stringResource(R.string.review_store_fallback, incident.storeId)
    val reason = humanize(incident.refusedCode)
    val attempts = incident.attempts
    MqCard(Modifier.testTag("review-incident-${incident.incidentId}")) {
        MqListRow(
            headline = stringResource(R.string.review_incident_headline, store, humanize(incident.sourceType)),
            supporting =
            listOfNotNull(
                stringResource(R.string.review_incident_reason, reason),
                formatDate(incident.occurredAt),
                attempts?.let { pluralStringResource(R.plurals.review_attempts, it, it) }
            ).joinToString(" · "),
            horizontalPadding = Spacing.None,
            belowSupporting = {
                MqBadge(
                    stateLabel(incident.status),
                    tone =
                    when (incident.status) {
                        OPEN -> MqTone.Danger
                        ACKNOWLEDGED -> MqTone.Warning
                        else -> MqTone.Success
                    }
                )
            }
        )
        incident.history.forEach { entry ->
            Column(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}) {
                Text(
                    stringResource(R.string.review_history_entry, stateLabel(entry.status), formatDate(entry.at)),
                    style = MaterialTheme.typography.bodyMedium
                )
                entry.note?.takeIf { it.isNotBlank() }?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }
        error?.let {
            MqErrorPanel(
                it,
                friendly =
                when (it.code) {
                    "invalid_transition" -> stringResource(R.string.review_error_moved)
                    "forbidden_store" -> stringResource(R.string.review_error_forbidden_store)
                    "forbidden_role" -> stringResource(R.string.review_error_forbidden_role)
                    "incident_not_found" -> stringResource(R.string.review_error_gone)
                    else -> null
                },
                modifier = Modifier.testTag("review-error-${incident.incidentId}")
            )
        }
        val moves = movesFrom(incident.status)
        if (moves.isNotEmpty()) {
            MqTextField(
                note,
                { note = it.take(NOTE_MAX) },
                stringResource(R.string.review_note_label),
                enabled = !busy,
                modifier = Modifier.padding(top = Spacing.Sm)
            )
            moves.forEach { status ->
                MqButton(
                    stringResource(if (status == RESOLVED) R.string.review_resolve else R.string.review_acknowledge),
                    { onReview(incident, status, note) },
                    enabled = !busy,
                    loading = busy,
                    fullWidth = true,
                    variant = if (status == RESOLVED) MqButtonVariant.Primary else MqButtonVariant.Secondary,
                    modifier = Modifier.testTag("review-${status.lowercase()}-${incident.incidentId}")
                )
            }
        }
    }
}

@Composable
private fun SyncsPart(
    syncs: List<OutboxRow>?,
    currentUserId: String?,
    sending: Set<Long>,
    storeNames: Map<Int, String>,
    onDismiss: (OutboxRow) -> Unit,
    onSendNow: () -> Unit,
    now: Instant,
    zone: ZoneId
) {
    MqSection(stringResource(R.string.review_syncs_title), Modifier.testTag("review-syncs")) {
        when {
            syncs == null -> MqLoading(stringResource(R.string.outbox_loading))
            syncs.isEmpty() ->
                Text(
                    stringResource(R.string.review_syncs_none),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("review-syncs-none")
                )
            else -> {
                val sendable =
                    syncs.any { it.state == RowState.PENDING && it.userId == currentUserId && it.id !in sending }
                OutboxSummary(
                    waiting = syncs.count { it.state == RowState.PENDING },
                    needAttention = syncs.count { it.state == RowState.REFUSED },
                    onSendNow = if (sendable) onSendNow else null
                )
                syncs.forEach { row ->
                    OutboxItem(row, row.userId != currentUserId, row.id in sending, storeNames, onDismiss, now, zone)
                }
            }
        }
    }
}

@Composable
private fun PromotionsPart(state: PromotionsShownState, onRetry: () -> Unit) {
    MqSection(stringResource(R.string.review_promotions_title), Modifier.testTag("review-promotions")) {
        val error = state.error
        state.since?.let {
            Text(
                stringResource(R.string.review_promotions_window, formatDate(it.toString())),
                style = MaterialTheme.typography.bodyMedium
            )
        }
        Text(
            stringResource(R.string.review_promotions_note),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        when {
            state.loading -> MqLoading(stringResource(R.string.review_promotions_loading))
            error != null -> MqErrorPanel(error, onRetry = onRetry)
            state.rows.isEmpty() ->
                Text(
                    stringResource(R.string.review_promotions_none),
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.testTag("review-promotions-none")
                )
        }
        if (!state.loading && error == null) {
            state.rows.groupBy { it.storeId }.forEach { (storeId, rows) ->
                Text(
                    state.storeNames[storeId] ?: stringResource(R.string.review_store_fallback, storeId),
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.semantics { heading() }.testTag("review-store-$storeId")
                )
                rows.forEach { row ->
                    MqListRow(
                        headline = row.name ?: stringResource(R.string.review_campaign_fallback, row.campaignId),
                        supporting = pluralStringResource(R.plurals.review_times_shown, row.times, row.times),
                        horizontalPadding = Spacing.None,
                        divider = true,
                        modifier = Modifier.testTag("review-shown-$storeId-${row.campaignId}")
                    )
                }
            }
            if (state.total > state.counted) {
                Text(
                    stringResource(R.string.review_promotions_counted, state.counted, state.total),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("review-promotions-cut")
                )
            }
        }
    }
}
