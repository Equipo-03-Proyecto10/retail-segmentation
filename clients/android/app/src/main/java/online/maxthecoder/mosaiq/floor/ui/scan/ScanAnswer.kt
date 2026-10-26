package online.maxthecoder.mosaiq.floor.ui.scan

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
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
        Text(product.name, style = MaterialTheme.typography.titleLarge)
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Md), verticalAlignment = Alignment.Bottom) {
            Text(formatPrice(product.listPrice), style = MaterialTheme.typography.titleMedium)
            Text(
                stringResource(R.string.scan_sku, product.sku),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
            if (shown.stockError == null) StockBadge(shown.quantity)
            if (!product.isActive) MqBadge(stringResource(R.string.scan_retired), tone = MqTone.Warning)
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
        if (onAddProduct != null) {
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
            variant = if (onAddProduct != null) MqButtonVariant.Secondary else MqButtonVariant.Primary,
            fullWidth = true,
            leadingIcon = R.drawable.ic_lucide_scan_line
        )
    }
}

@Composable
private fun StockBadge(quantity: Int?) {
    when (stockStatus(quantity)) {
        StockStatus.IN_STOCK -> MqBadge(stringResource(R.string.scan_in_stock), tone = MqTone.Success)
        StockStatus.OUT_OF_STOCK -> MqBadge(stringResource(R.string.scan_out_of_stock), tone = MqTone.Danger)
        StockStatus.NOT_STOCKED -> MqBadge(stringResource(R.string.scan_not_stocked), tone = MqTone.Neutral)
    }
}

@Composable
private fun stockLine(quantity: Int?, storeName: String): String = when {
    quantity == null -> stringResource(R.string.scan_not_stocked_message, storeName)
    quantity <= 0 -> stringResource(R.string.scan_none_at_store, storeName)
    else -> pluralStringResource(R.plurals.scan_units_at_store, quantity, quantity, storeName)
}
