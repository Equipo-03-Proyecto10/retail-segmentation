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
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.outbox.asError
import online.maxthecoder.mosaiq.floor.outbox.refusalAdvice
import online.maxthecoder.mosaiq.floor.outbox.refusalReason
import online.maxthecoder.mosaiq.floor.ui.SaleBuilderState
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
        ui.history.forEach { row -> SaleHistoryRow(row, ui, actions) }
        ui.statusMessage?.let {
            Text(
                it,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.semantics {
                    liveRegion = LiveRegionMode.Polite
                }
            )
        }
        ui.statusError?.let { MqErrorPanel(it) }
    }
}

@Composable
private fun SaleHistoryRow(row: OutboxRow, ui: SaleUi, actions: SaleActions) {
    Column(
        Modifier.fillMaxWidth().testTag("sale-row-${row.id}"),
        verticalArrangement = Arrangement.spacedBy(Spacing.Sm)
    ) {
        Row(
            Modifier.semantics(mergeDescendants = true) {},
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Spacing.Md)
        ) {
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
                    row.asError(stringResource(R.string.sale_refused_default)),
                    friendly = refusalReason(row.code)
                )
                refusalAdvice(row.code)?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                RefusedActions(row, ui, actions)
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

/**
 * What the seller can do with a refused sale, wherever it sits in the history: put its cart back to fix it, or
 * throw it away. Correcting never replaces a sale in progress, and says why when it cannot be offered (F27-13).
 */
@Composable
private fun RefusedActions(row: OutboxRow, ui: SaleUi, actions: SaleActions) {
    val whenText = formatWhen(row.createdAt)
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
        when (correctionOf(row, ui)) {
            Correction.AVAILABLE -> MqButton(
                stringResource(R.string.sale_correct_row, whenText),
                { actions.onCorrect(row) },
                fullWidth = true,
                modifier = Modifier.testTag("correct-${row.id}")
            )
            Correction.OPEN -> Text(
                stringResource(R.string.sale_correct_open),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag("correct-open-${row.id}")
            )
            Correction.BUSY -> Text(
                stringResource(R.string.sale_correct_busy),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag("correct-busy-${row.id}")
            )
            Correction.GONE -> Text(
                stringResource(R.string.sale_correct_gone),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag("correct-gone-${row.id}")
            )
        }
        MqButton(
            stringResource(R.string.sale_discard_row, whenText),
            { actions.onDiscard(row) },
            variant = MqButtonVariant.Quiet,
            fullWidth = true,
            modifier = Modifier.testTag("discard-${row.id}")
        )
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
                SaleTotals(sale)
                if (sale.queuedRowId != null) {
                    when (ui.queued?.state) {
                        RowState.REFUSED -> {
                            Text(
                                stringResource(R.string.sale_refused_title),
                                style = MaterialTheme.typography.bodyMedium,
                                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }
                            )
                            MqButton(
                                stringResource(R.string.sale_back_to_cart),
                                actions.onBackToCart,
                                fullWidth = true,
                                modifier = Modifier.testTag("back-to-cart")
                            )
                            MqButton(
                                stringResource(R.string.sale_another),
                                actions.onNewSale,
                                fullWidth = true,
                                variant = MqButtonVariant.Quiet
                            )
                        }
                        else -> {
                            Text(
                                ui.queued?.let(actions.rowStatus) ?: stringResource(R.string.sale_saved),
                                style = MaterialTheme.typography.bodyMedium,
                                modifier = Modifier.testTag("sale-saved").semantics {
                                    liveRegion = LiveRegionMode.Polite
                                }
                            )
                            MqButton(stringResource(R.string.sale_another), actions.onNewSale, fullWidth = true)
                        }
                    }
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
                        actions.onReview,
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

/** The total, and above it the subtotal and the discount when a promotion takes something off. */
@Composable
internal fun SaleTotals(sale: SaleBuilderState) {
    val discount = saleDiscount(sale)
    if (discount.signum() > 0) {
        AmountRow(stringResource(R.string.sale_subtotal), formatMoney(saleSubtotal(sale)), "sale-subtotal")
        AmountRow(
            stringResource(R.string.sale_discount),
            stringResource(R.string.sale_discount_amount, formatMoney(discount)),
            "sale-discount"
        )
    }
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
}

@Composable
internal fun AmountRow(label: String, amount: String, tag: String) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        Text(amount, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.testTag(tag))
    }
}
