package online.maxthecoder.mosaiq.floor.ui.scan

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.CatalogSearch
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqEmptyState
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqTextField
import online.maxthecoder.mosaiq.floor.ui.formatMoney
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * Finds a product by name or SKU (F27-04). A row opens the product's detail with
 * its status and availability at the checked-in store; it queues no QR_SCAN.
 */
@Composable
fun CatalogSearchSection(state: CatalogSearch, onSearch: (String) -> Unit, onOpen: (Int) -> Unit) {
    var query by rememberSaveable { mutableStateOf("") }
    val submit = { if (query.isNotBlank()) onSearch(query) }
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
        Text(
            stringResource(R.string.scan_search_title),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        MqTextField(
            value = query,
            onValueChange = { query = it },
            label = stringResource(R.string.scan_search_label),
            supportingText = stringResource(R.string.scan_search_help),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { submit() }),
            modifier = Modifier.testTag("catalog-query")
        )
        MqButton(
            stringResource(R.string.scan_search),
            { submit() },
            variant = MqButtonVariant.Secondary,
            enabled = query.isNotBlank() && state != CatalogSearch.Searching,
            fullWidth = true
        )
        when (state) {
            CatalogSearch.Idle -> Unit
            CatalogSearch.Searching -> MqLoading(stringResource(R.string.scan_searching))
            is CatalogSearch.Failed -> MqErrorPanel(state.error, onRetry = { submit() })
            is CatalogSearch.Found -> Found(state, onOpen)
        }
    }
}

@Composable
private fun Found(found: CatalogSearch.Found, onOpen: (Int) -> Unit) {
    if (found.products.isEmpty()) {
        MqEmptyState(
            icon = R.drawable.ic_lucide_package,
            title = stringResource(R.string.scan_search_empty_title),
            message = stringResource(R.string.scan_search_empty_message),
            modifier = Modifier.testTag("catalog-empty")
        )
        return
    }
    Column(Modifier.testTag("catalog-results")) {
        found.products.forEach { product ->
            MqListRow(
                headline = product.name,
                supporting =
                if (product.isActive) {
                    stringResource(R.string.scan_search_row, product.sku, formatMoney(product.listPrice))
                } else {
                    stringResource(R.string.scan_retired)
                },
                onClick = { onOpen(product.productId) },
                chevron = true,
                divider = true,
                modifier = Modifier.testTag("catalog-product-${product.productId}")
            )
        }
    }
    if (found.total > found.products.size) {
        Text(
            stringResource(R.string.scan_search_more, found.products.size, found.total),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}
