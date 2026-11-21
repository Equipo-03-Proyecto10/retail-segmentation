package online.maxthecoder.mosaiq.floor.ui.outbox

import androidx.annotation.DrawableRes
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.time.Instant
import java.time.ZoneId
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.outbox.asError
import online.maxthecoder.mosaiq.floor.outbox.refusalAdvice
import online.maxthecoder.mosaiq.floor.outbox.refusalReason
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqEmptyState
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqIcon
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.formatClock
import online.maxthecoder.mosaiq.floor.ui.formatMediumDay
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

@Composable
fun OutboxList(model: FloorViewModel) {
    val rows by model.open.collectAsStateWithLifecycle()
    val userId by model.userId.collectAsStateWithLifecycle()
    val chosen by model.chosen.collectAsStateWithLifecycle()
    val stores by model.stores.collectAsStateWithLifecycle()
    val sending by model.sending.collectAsStateWithLifecycle()
    // Store names already read: the chosen store and the last page of the picker.
    val names =
        buildMap {
            stores.stores.forEach { put(it.storeId, it.name) }
            chosen?.let { put(it.storeId, it.name) }
        }
    OutboxContent(rows, userId, names, onDismiss = model::dismiss, sending = sending, onSendNow = model::sendNow)
}

/** The outbox: stateless, so previews and tests can drive every state. */
@Composable
fun OutboxContent(
    rows: List<OutboxRow>,
    currentUserId: String?,
    storeNames: Map<Int, String>,
    onDismiss: (OutboxRow) -> Unit,
    now: Instant = Instant.now(),
    zone: ZoneId = ZoneId.systemDefault(),
    sending: Set<Long> = emptySet(),
    onSendNow: (() -> Unit)? = null
) {
    if (rows.isEmpty()) {
        MqEmptyState(
            icon = R.drawable.ic_lucide_circle_check,
            title = stringResource(R.string.outbox_empty_title),
            message = stringResource(R.string.outbox_empty_message)
        )
        return
    }
    // Waiting rows of another user cannot go out under this session, so "Send now" is not for them.
    val waiting = rows.count { it.state == RowState.PENDING }
    val sendable =
        rows.any { it.state == RowState.PENDING && it.userId == currentUserId && it.id !in sending }
    val needAttention = rows.count { it.state == RowState.REFUSED }
    LazyColumn(modifier = Modifier.fillMaxSize()) {
        item(key = "summary") {
            OutboxSummary(waiting, needAttention, if (sendable) onSendNow else null)
        }
        items(rows, key = { it.id }) { row ->
            OutboxItem(row, row.userId != currentUserId, row.id in sending, storeNames, onDismiss, now, zone)
        }
    }
}

/** How many writes wait and how many need attention, with the way to try sending now. */
@Composable
private fun OutboxSummary(waiting: Int, needAttention: Int, onSendNow: (() -> Unit)?) {
    Column(
        modifier = Modifier.fillMaxWidth().padding(horizontal = Spacing.Lg, vertical = Spacing.Sm),
        verticalArrangement = Arrangement.spacedBy(Spacing.Sm)
    ) {
        val lines =
            listOfNotNull(
                if (waiting > 0) pluralStringResource(R.plurals.outbox_summary_waiting, waiting, waiting) else null,
                if (needAttention > 0) {
                    pluralStringResource(R.plurals.outbox_summary_attention, needAttention, needAttention)
                } else {
                    null
                }
            )
        if (lines.isNotEmpty()) {
            Text(
                lines.joinToString(" · "),
                style = MaterialTheme.typography.titleSmall,
                modifier = Modifier.semantics { heading() }
            )
        }
        if (onSendNow != null) {
            MqButton(
                stringResource(R.string.outbox_send_now),
                onSendNow,
                variant = MqButtonVariant.Secondary,
                leadingIcon = R.drawable.ic_lucide_refresh_cw
            )
        }
    }
}

@Composable
private fun OutboxItem(
    row: OutboxRow,
    otherUser: Boolean,
    sending: Boolean,
    storeNames: Map<Int, String>,
    onDismiss: (OutboxRow) -> Unit,
    now: Instant,
    zone: ZoneId
) {
    val refused = row.state == RowState.REFUSED
    val supporting =
        when {
            refused -> humanTime(row.createdAt, now, zone)
            otherUser -> stringResource(R.string.outbox_other_user)
            row.attempts > 0 ->
                humanTime(row.createdAt, now, zone) + " · " +
                    pluralStringResource(R.plurals.outbox_attempts, row.attempts, row.attempts)
            else -> humanTime(row.createdAt, now, zone)
        }
    Column(modifier = Modifier.fillMaxWidth()) {
        MqListRow(
            headline = title(row, storeNames),
            supporting = supporting,
            divider = !refused,
            leading = {
                MqIcon(
                    kindIcon(row.operation),
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
            },
            leadingAlignment = Alignment.Top,
            belowSupporting = { StateBadge(outboxStatus(row.state, sending)) }
        )
        if (refused) {
            Column(
                modifier = Modifier.padding(horizontal = Spacing.Lg, vertical = Spacing.Sm),
                verticalArrangement = Arrangement.spacedBy(Spacing.Sm)
            ) {
                // The reason in plain words; the code, fields and correlation id sit behind "Details".
                MqErrorPanel(row.asError(), friendly = refusalReason(row.code))
                refusalAdvice(row.code)?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                MqButton(
                    stringResource(R.string.outbox_dismiss),
                    { onDismiss(row) },
                    variant = MqButtonVariant.Secondary
                )
            }
        }
    }
}

@Composable
private fun StateBadge(status: OutboxStatus) {
    when (status) {
        OutboxStatus.WAITING ->
            MqBadge(
                stringResource(R.string.outbox_state_waiting),
                tone = MqTone.Warning,
                leadingIcon = R.drawable.ic_lucide_clock
            )
        OutboxStatus.SENDING ->
            MqBadge(
                stringResource(R.string.outbox_state_sending),
                tone = MqTone.Info,
                leadingIcon = R.drawable.ic_lucide_clock
            )
        OutboxStatus.SENT ->
            MqBadge(
                stringResource(R.string.outbox_state_sent),
                tone = MqTone.Success,
                leadingIcon = R.drawable.ic_lucide_circle_check
            )
        OutboxStatus.NEEDS_ATTENTION ->
            MqBadge(
                stringResource(R.string.outbox_state_attention),
                tone = MqTone.Danger,
                leadingIcon = R.drawable.ic_lucide_triangle_alert
            )
    }
}

@DrawableRes
private fun kindIcon(operation: Operation): Int = when (operation) {
    Operation.CHECK_IN -> R.drawable.ic_lucide_map_pin
    Operation.SALE -> R.drawable.ic_lucide_shopping_cart
    Operation.QR_SCAN -> R.drawable.ic_lucide_scan_line
    Operation.RECO_CLICK -> R.drawable.ic_lucide_package
    Operation.CAMPAIGN_EXPOSURE -> R.drawable.ic_lucide_eye
}

@Composable
private fun title(row: OutboxRow, storeNames: Map<Int, String>): String = when (row.operation) {
    Operation.CHECK_IN -> {
        val storeId =
            runCatching {
                json.parseToJsonElement(
                    row.body
                ).jsonObject["store_id"]?.jsonPrimitive?.intOrNull
            }.getOrNull()
        val name = storeId?.let { storeNames[it] }
        if (name != null) {
            stringResource(R.string.outbox_kind_check_in_at, name)
        } else {
            stringResource(R.string.outbox_kind_check_in)
        }
    }
    Operation.SALE -> stringResource(R.string.outbox_kind_sale)
    Operation.QR_SCAN -> stringResource(R.string.outbox_kind_scan)
    Operation.RECO_CLICK -> stringResource(R.string.outbox_kind_reco)
    Operation.CAMPAIGN_EXPOSURE -> stringResource(R.string.outbox_kind_exposure)
}

/** "Today, 14:32", "Yesterday, 14:32", or the date and time for anything older. */
@Composable
internal fun humanTime(epochMillis: Long, now: Instant, zone: ZoneId): String {
    val at = Instant.ofEpochMilli(epochMillis).atZone(zone)
    val clock = formatClock(epochMillis, zone)
    val today = now.atZone(zone).toLocalDate()
    return when (at.toLocalDate()) {
        today -> stringResource(R.string.outbox_today, clock)
        today.minusDays(1) -> stringResource(R.string.outbox_yesterday, clock)
        else -> stringResource(R.string.outbox_day_and_time, formatMediumDay(at), clock)
    }
}

private val PreviewNow: Instant = Instant.parse("2026-10-26T20:00:00Z")

private fun sample(id: Long, operation: Operation, state: RowState, attempts: Int = 0) = OutboxRow(
    id = id,
    operation = operation,
    path = "/v1/x",
    body = "{}",
    clientId = "c$id",
    userId = "A",
    createdAt = PreviewNow.toEpochMilli() - id * 3_600_000,
    state = state,
    attempts = attempts,
    code = if (state == RowState.REFUSED) "validation_failed" else null,
    // ui-string-ok: preview
    message = if (state == RowState.REFUSED) "The sale has no items." else null
)

@Composable
private fun OutboxPreview(dark: Boolean, rows: List<OutboxRow>) = MosaiqTheme(darkTheme = dark) {
    Surface {
        Column(Modifier.fillMaxSize().padding(Spacing.Lg)) {
            OutboxContent(rows, "A", emptyMap(), {}, PreviewNow, ZoneId.of("UTC"), sending = setOf(3L), onSendNow = {})
        }
    }
}

@Preview(name = "Outbox rows, light", widthDp = 375, heightDp = 640, showBackground = true)
@Composable
private fun RowsLight() = OutboxPreview(
    false,
    listOf(
        sample(1, Operation.CHECK_IN, RowState.PENDING, attempts = 2),
        sample(2, Operation.SALE, RowState.REFUSED),
        sample(3, Operation.QR_SCAN, RowState.PENDING)
    )
)

@Preview(name = "Outbox rows, dark", widthDp = 375, heightDp = 640, showBackground = true)
@Composable
private fun RowsDark() = OutboxPreview(
    true,
    listOf(
        sample(1, Operation.CHECK_IN, RowState.PENDING, attempts = 1),
        sample(2, Operation.SALE, RowState.REFUSED),
        sample(3, Operation.QR_SCAN, RowState.PENDING),
        sample(4, Operation.SALE, RowState.DONE)
    )
)

@Preview(name = "Outbox empty, light", widthDp = 375, heightDp = 640, showBackground = true)
@Composable
private fun EmptyLight() = OutboxPreview(false, emptyList())

@Preview(name = "Outbox empty, dark", widthDp = 375, heightDp = 640, showBackground = true)
@Composable
private fun EmptyDark() = OutboxPreview(true, emptyList())
