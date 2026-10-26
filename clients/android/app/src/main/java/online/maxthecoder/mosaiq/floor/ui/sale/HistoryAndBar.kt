package online.maxthecoder.mosaiq.floor.ui.sale

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.outbox.refusalAdvice
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqSection
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.formatMoney
import online.maxthecoder.mosaiq.floor.ui.formatWhen
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** The sales recorded on this phone, each with a status badge that always carries text. */
@Composable
fun HistorySection(ui: SaleUi, actions: SaleActions) {
    MqSection(stringResource(R.string.sale_history_title)) {
        if (ui.history.isEmpty()) {
            Text(
                stringResource(R.string.sale_history_empty),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag("history-empty")
            )
        }
        ui.history.forEach { row -> SaleHistoryRow(row, actions) }
        ui.statusMessage?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
        ui.statusError?.let { MqErrorPanel(it) }
    }
}

@Composable
private fun SaleHistoryRow(row: OutboxRow, actions: SaleActions) {
    Column(
        Modifier.fillMaxWidth().testTag("sale-row-${row.id}"),
        verticalArrangement = Arrangement.spacedBy(Spacing.Sm)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Spacing.Md)) {
            Text(
                stringResource(R.string.sale_history_row, formatWhen(row.createdAt)),
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.weight(1f)
            )
            when (row.state) {
                RowState.PENDING -> MqBadge(
                    stringResource(R.string.sale_state_waiting),
                    tone = MqTone.Warning,
                    leadingIcon = R.drawable.ic_lucide_clock
                )
                RowState.REFUSED -> MqBadge(
                    stringResource(R.string.sale_state_attention),
                    tone = MqTone.Danger,
                    leadingIcon = R.drawable.ic_lucide_triangle_alert
                )
                RowState.DONE -> MqBadge(
                    stringResource(R.string.sale_state_sent),
                    tone = MqTone.Success,
                    leadingIcon = R.drawable.ic_lucide_circle_check
                )
            }
        }
        when (row.state) {
            RowState.PENDING -> Text(
                stringResource(R.string.sale_history_pending),
                style = MaterialTheme.typography.bodyMedium
            )
            RowState.REFUSED -> {
                MqErrorPanel(
                    ApiError(row.status ?: 0, row.code ?: "refused", row.message ?: "Sale refused.", row.correlationId)
                )
                refusalAdvice(row.code)?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
            }
            RowState.DONE -> {
                Text(
                    actions.rowStatus(row) ?: stringResource(R.string.sale_history_recorded),
                    style = MaterialTheme.typography.bodyMedium
                )
                MqButton(
                    stringResource(R.string.sale_check_status),
                    { actions.onRefreshStatus(row) },
                    variant = MqButtonVariant.Quiet
                )
            }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    }
}

/** Why "Record sale" is off, in words; null when it can be pressed. */
@Composable
internal fun recordBlocker(ui: SaleUi): String? = when {
    ui.store == null -> stringResource(R.string.sale_blocker_store)
    ui.customer == null -> stringResource(R.string.sale_blocker_customer)
    ui.sale.lines.isEmpty() -> stringResource(R.string.sale_blocker_items)
    ui.registration.channels.none { it.name == PHYSICAL_STORE } ->
        stringResource(if (ui.registration.loading) R.string.sale_channels_loading else R.string.sale_blocker_channels)
    else -> null
}

/** Section 3, pinned above the bottom navigation: the total and the one primary action. */
@Composable
fun SaleBar(ui: SaleUi, actions: SaleActions, modifier: Modifier = Modifier) {
    val sale = ui.sale
    val blocker = recordBlocker(ui)
    Surface(modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.surface) {
        Column {
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Column(Modifier.padding(vertical = Spacing.Md), verticalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        stringResource(R.string.sale_total),
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.weight(1f)
                    )
                    Text(
                        formatMoney(saleTotal(sale)),
                        style = MaterialTheme.typography.titleLarge,
                        modifier = Modifier.testTag("sale-total")
                    )
                }
                if (sale.queuedRowId != null) {
                    Text(
                        stringResource(R.string.sale_saved),
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.testTag("sale-saved")
                    )
                    MqButton(stringResource(R.string.sale_another), actions.onNewSale, fullWidth = true)
                } else {
                    if (blocker != null) {
                        if (ui.registration.loading && ui.registration.channels.isEmpty()) {
                            MqLoading(blocker)
                        } else {
                            Text(
                                blocker,
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.testTag("record-blocker")
                            )
                        }
                        if (ui.registration.channels.isEmpty() && !ui.registration.loading) {
                            MqButton(
                                stringResource(R.string.sale_channels_load),
                                actions.onLoadChannels,
                                variant = MqButtonVariant.Quiet
                            )
                        }
                    }
                    MqButton(
                        stringResource(R.string.sale_record),
                        actions.onConfirm,
                        enabled = blocker == null,
                        loading = sale.confirming,
                        fullWidth = true,
                        modifier = Modifier.testTag("record-sale")
                    )
                }
            }
        }
    }
}
