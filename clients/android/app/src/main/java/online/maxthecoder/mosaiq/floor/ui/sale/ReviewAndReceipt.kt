package online.maxthecoder.mosaiq.floor.ui.sale

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.SaleLine
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqCard
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.formatMoney
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** The summary before the sale is recorded, in a modal bottom sheet; dismissing it goes back to the cart. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReviewSheet(ui: SaleUi, actions: SaleActions) {
    ModalBottomSheet(
        onDismissRequest = actions.onCancelReview,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ) {
        ReviewContent(ui, actions)
    }
}

/**
 * The lines with the price charged and the promotion each names, the subtotal, the discount and the
 * total, and the one tap that records the sale. That tap is where the client id is generated.
 */
@Composable
fun ReviewContent(ui: SaleUi, actions: SaleActions) {
    val sale = ui.sale
    val cap = sale.discounts.maxDiscountPercent
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = Spacing.Lg)
            .padding(bottom = Spacing.Xxl).testTag("sale-review"),
        verticalArrangement = Arrangement.spacedBy(Spacing.Md)
    ) {
        Text(
            stringResource(R.string.sale_review_title),
            style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.semantics { heading() }
        )
        ui.customer?.let {
            Text(stringResource(R.string.sale_review_customer, it.name), style = MaterialTheme.typography.bodyLarge)
        }
        sale.lines.forEach { line -> SummaryLine(line, chargedPrice(line, cap).toPlainString(), cap) }
        SaleTotals(sale)
        Text(
            stringResource(R.string.sale_review_help),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        sale.error?.let { MqErrorPanel(it) }
        MqButton(
            stringResource(R.string.sale_confirm),
            actions.onConfirm,
            enabled = sale.queuedRowId == null,
            loading = sale.confirming,
            fullWidth = true,
            modifier = Modifier.testTag("confirm-sale")
        )
        MqButton(
            stringResource(R.string.sale_back_to_cart),
            actions.onCancelReview,
            enabled = !sale.confirming,
            fullWidth = true,
            variant = MqButtonVariant.Quiet
        )
    }
}

@Composable
private fun SummaryLine(line: SaleLine, unitPrice: String, cap: String?) {
    Column(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}.testTag("summary-${line.product.productId}")) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(line.product.name, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
            Text(formatMoney(lineTotal(line, cap)), style = MaterialTheme.typography.bodyLarge)
        }
        Text(
            stringResource(R.string.sale_review_line, line.quantity, formatMoney(unitPrice)),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        line.promotion?.let {
            Text(it.name, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.primary)
        }
    }
}

/**
 * What became of the confirmed sale: its receipt with the sale id once recorded, or, when refused,
 * why in plain words and which line, with the cart kept for a fix. Nothing while it waits to send.
 */
@Composable
fun SaleOutcome(ui: SaleUi, actions: SaleActions = SaleActions()) {
    val receipt = receiptOf(ui.queued, ui.sale.lines)
    val refusal = refusalOf(ui.queued, ui.sale.lines)
    when {
        receipt != null -> ReceiptCard(receipt)
        refusal != null -> RefusalCard(refusal, onDiscard = { ui.queued?.let(actions.onDiscard) })
    }
}

@Composable
private fun ReceiptCard(receipt: Receipt) {
    MqCard(Modifier.testTag("sale-receipt")) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Spacing.Md)) {
            Text(
                stringResource(R.string.sale_receipt_title),
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f).semantics { heading() }
            )
            MqBadge(
                stringResource(R.string.sale_state_sent),
                tone = MqTone.Success,
                leadingIcon = R.drawable.ic_lucide_circle_check
            )
        }
        Text(
            stringResource(R.string.sale_receipt_id, receipt.saleId),
            style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.testTag("receipt-sale-id").semantics { liveRegion = LiveRegionMode.Polite }
        )
        if (receipt.replayed) {
            Text(
                stringResource(R.string.sale_status_replayed, receipt.saleId),
                style = MaterialTheme.typography.bodyMedium
            )
        }
        receipt.lines.forEach { line ->
            Column(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}) {
                Text(
                    line.name ?: stringResource(R.string.sale_product_fallback, line.productId),
                    style = MaterialTheme.typography.bodyLarge
                )
                Text(
                    stringResource(R.string.sale_review_line, line.quantity, formatMoney(line.unitPrice)),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                line.promotion?.let {
                    Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.primary)
                }
            }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        receipt.discount?.takeIf { it.signum() > 0 }?.let {
            AmountRow(
                stringResource(R.string.sale_discount),
                stringResource(R.string.sale_discount_amount, formatMoney(it)),
                "receipt-discount"
            )
        }
        AmountRow(stringResource(R.string.sale_total), formatMoney(receipt.total), "receipt-total")
    }
}

@Composable
private fun RefusalCard(refusal: SaleRefusal, onDiscard: () -> Unit) {
    Column(Modifier.fillMaxWidth().testTag("sale-refused"), verticalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
        Text(
            stringResource(R.string.sale_refused_title),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        MqErrorPanel(refusal.error, friendly = refusal.reason)
        refusal.line?.let {
            Text(stringResource(R.string.sale_refused_line, it), style = MaterialTheme.typography.bodyLarge)
        }
        // "Back to cart" sits in the bar below, always in reach.
        Text(stringResource(R.string.sale_refused_kept), style = MaterialTheme.typography.bodyMedium)
        MqButton(
            stringResource(R.string.sale_discard),
            onDiscard,
            variant = MqButtonVariant.Quiet,
            fullWidth = true,
            modifier = Modifier.testTag("discard-sale")
        )
    }
}
