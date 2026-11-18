package online.maxthecoder.mosaiq.floor.ui.scan

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.ui.ScanResult
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqCard
import online.maxthecoder.mosaiq.floor.ui.components.MqEmptyState
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.formatMoney
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * What the last code came to: nothing yet, a wait, a friendly dead end, a retryable
 * failure, or the product. Stateless, so every state can be previewed and tested.
 */
@Composable
fun ScanAnswer(
    result: ScanResult,
    onScanAnother: () -> Unit,
    onRetry: () -> Unit,
    onPickStore: () -> Unit,
    onAddProduct: ((Product) -> Unit)?
) {
    when (result) {
        ScanResult.Idle -> Unit
        ScanResult.Looking -> MqLoading(stringResource(R.string.scan_looking))
        ScanResult.NoStore ->
            MqEmptyState(
                icon = R.drawable.ic_lucide_store,
                title = stringResource(R.string.scan_no_store_title),
                message = stringResource(R.string.scan_no_store_message),
                actionLabel = stringResource(R.string.scan_pick_store),
                onAction = onPickStore
            )
        ScanResult.NotAProduct ->
            MqEmptyState(
                icon = R.drawable.ic_lucide_scan_line,
                title = stringResource(R.string.scan_not_a_product_title),
                message = stringResource(R.string.scan_not_a_product_message),
                actionLabel = stringResource(R.string.scan_scan_another),
                onAction = onScanAnother
            )
        ScanResult.ProductNotFound ->
            MqEmptyState(
                icon = R.drawable.ic_lucide_package,
                title = stringResource(R.string.scan_not_found_title),
                message = stringResource(R.string.scan_not_found_message),
                actionLabel = stringResource(R.string.scan_scan_another),
                onAction = onScanAnother
            )
        is ScanResult.Failed -> FailedLookup(result.error, onRetry, onScanAnother)
        is ScanResult.Shown -> ProductCard(result, onScanAnother, onAddProduct)
    }
}

@Composable
private fun FailedLookup(error: ApiError, onRetry: () -> Unit, onScanAnother: () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.Md)) {
        MqErrorPanel(
            error,
            onRetry = onRetry,
            friendly = if (error.code == "unreachable") stringResource(R.string.scan_offline) else null
        )
        MqButton(
            stringResource(R.string.scan_scan_another),
            onScanAnother,
            variant = MqButtonVariant.Quiet,
            fullWidth = true
        )
    }
}

@Composable
private fun ProductCard(shown: ScanResult.Shown, onScanAnother: () -> Unit, onAddProduct: ((Product) -> Unit)?) {
    val product = shown.product
    MqCard {
        Text(
            product.name,
            style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.semantics { heading() }
        )
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Md), verticalAlignment = Alignment.Bottom) {
            Text(formatMoney(product.listPrice), style = MaterialTheme.typography.titleMedium)
            Text(
                skuLabel(product.sku),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        val sellable = canBeSold(product.isActive, shown.status)
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
            if (shown.stockError == null) StockBadge(shown.quantity, shown.inStore?.reason)
            if (!sellable) StatusBadge(shown.status)
        }
        if (!sellable) {
            val discontinued = shown.status == DISCONTINUED
            val reason = if (discontinued) R.string.scan_discontinued_message else R.string.scan_unsellable_message
            Text(
                stringResource(reason),
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.testTag("not-sellable")
            )
        }
        if (shown.stockError != null) {
            MqErrorPanel(shown.stockError)
        } else {
            Text(
                stockLine(shown.quantity, shown.storeName),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        if (onAddProduct != null && sellable) {
            MqButton(
                stringResource(R.string.scan_add_to_sale),
                { onAddProduct(product) },
                fullWidth = true,
                leadingIcon = R.drawable.ic_lucide_shopping_cart
            )
        }
        MqButton(
            stringResource(R.string.scan_scan_another),
            onScanAnother,
            variant = if (onAddProduct != null && sellable) MqButtonVariant.Secondary else MqButtonVariant.Primary,
            fullWidth = true,
            leadingIcon = R.drawable.ic_lucide_scan_line
        )
    }
}

@Composable
private fun StockBadge(quantity: Int?, reason: String?) {
    when (stockStatus(quantity, reason)) {
        StockStatus.IN_STOCK -> MqBadge(stringResource(R.string.scan_in_stock), tone = MqTone.Success)
        StockStatus.OUT_OF_STOCK -> MqBadge(stringResource(R.string.scan_out_of_stock), tone = MqTone.Danger)
        StockStatus.NOT_STOCKED -> MqBadge(stringResource(R.string.scan_not_stocked), tone = MqTone.Neutral)
        StockStatus.NOT_SELLABLE -> MqBadge(stringResource(R.string.scan_not_sellable), tone = MqTone.Neutral)
        StockStatus.CHANNEL_NOT_ENABLED ->
            MqBadge(stringResource(R.string.scan_channel_not_enabled), tone = MqTone.Neutral)
    }
}

/** The commercial status of a product that cannot be sold; a product without one says it is no longer sold. */
@Composable
private fun StatusBadge(status: String?) {
    when (status) {
        DISCONTINUED -> MqBadge(stringResource(R.string.scan_discontinued), tone = MqTone.Danger)
        PAUSED -> MqBadge(stringResource(R.string.scan_paused), tone = MqTone.Warning)
        else -> MqBadge(stringResource(R.string.scan_retired), tone = MqTone.Warning)
    }
}

private const val DISCONTINUED = "discontinued"
private const val PAUSED = "paused"

@Composable
private fun stockLine(quantity: Int?, storeName: String): String = when {
    quantity == null -> stringResource(R.string.scan_not_stocked_message, storeName)
    quantity <= 0 -> stringResource(R.string.scan_none_at_store, storeName)
    else -> pluralStringResource(R.plurals.scan_units_at_store, quantity, quantity, storeName)
}

/** "SKU 120" for a bare code; a code that already starts with "SKU" is shown as is. */
@Composable
private fun skuLabel(sku: String): String =
    if (sku.startsWith("SKU", ignoreCase = true)) sku else stringResource(R.string.scan_sku, sku)
