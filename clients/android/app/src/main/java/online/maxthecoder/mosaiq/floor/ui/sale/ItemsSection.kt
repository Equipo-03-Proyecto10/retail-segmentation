package online.maxthecoder.mosaiq.floor.ui.sale

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.SaleLine
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqEmptyState
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqIcon
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqQuantityStepper
import online.maxthecoder.mosaiq.floor.ui.components.MqSection
import online.maxthecoder.mosaiq.floor.ui.components.MqTextField
import online.maxthecoder.mosaiq.floor.ui.formatMoney
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** Section 2: search products to add, and the cart as rows with a quantity stepper. */
@Composable
fun ItemsSection(ui: SaleUi, actions: SaleActions) {
    val sale = ui.sale
    var query by rememberSaveable { mutableStateOf(sale.query) }
    var searched by rememberSaveable { mutableStateOf(sale.products.isNotEmpty()) }
    val locked = sale.confirming || sale.queuedRowId != null
    val search = {
        if (query.isNotBlank()) {
            searched = true
            actions.onSearchProducts(query)
        }
    }
    MqSection(stringResource(R.string.sale_items_title)) {
        MqTextField(
            query,
            { query = it },
            stringResource(R.string.sale_product_label),
            supportingText = stringResource(R.string.sale_product_help),
            enabled = !locked,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { search() }),
            modifier = Modifier.testTag("product-query")
        )
        MqButton(
            stringResource(R.string.sale_product_search),
            search,
            enabled = query.isNotBlank() && !locked,
            loading = sale.loading,
            fullWidth = true,
            variant = MqButtonVariant.Secondary
        )
        when {
            sale.loading -> MqLoading(stringResource(R.string.sale_product_loading))
            sale.error != null -> MqErrorPanel(sale.error, onRetry = if (searched) search else null)
            searched && sale.products.isEmpty() ->
                MqEmptyState(
                    icon = R.drawable.ic_lucide_package,
                    title = stringResource(R.string.sale_product_empty_title),
                    message = stringResource(R.string.sale_product_empty_help),
                    modifier = Modifier.testTag("products-empty")
                )
        }
        if (!sale.loading) {
            sale.products.forEach { product ->
                MqListRow(
                    headline = product.name,
                    supporting = if (product.isActive) {
                        formatMoney(
                            product.listPrice
                        )
                    } else {
                        stringResource(R.string.sale_product_inactive)
                    },
                    onClick = { actions.onAddProduct(product) },
                    enabled = product.isActive && !locked,
                    divider = true,
                    trailing = {
                        MqIcon(
                            R.drawable.ic_lucide_plus,
                            contentDescription = stringResource(R.string.sale_add_item, product.name)
                        )
                    }
                )
            }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        Text(stringResource(R.string.sale_cart_title), style = MaterialTheme.typography.titleSmall)
        if (sale.lines.isEmpty()) {
            MqEmptyState(
                icon = R.drawable.ic_lucide_shopping_cart,
                title = stringResource(R.string.sale_cart_empty_title),
                message = stringResource(R.string.sale_cart_empty_help),
                modifier = Modifier.testTag("cart-empty")
            )
        }
        sale.lines.forEach { line -> CartLine(line, locked, actions) }
    }
}

@Composable
private fun CartLine(line: SaleLine, locked: Boolean, actions: SaleActions) {
    val id = line.product.productId
    Column(Modifier.fillMaxWidth().testTag("cart-line-$id"), verticalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
        Text(line.product.name, style = MaterialTheme.typography.bodyLarge)
        Text(
            stringResource(R.string.sale_unit_price, formatMoney(line.product.listPrice)),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Spacing.Md)) {
            MqQuantityStepper(
                value = line.quantity,
                onDecrease = { actions.onSetQuantity(id, line.quantity - 1) },
                onIncrease = { actions.onSetQuantity(id, line.quantity + 1) },
                modifier = Modifier.testTag("stepper-$id")
            )
            Text(
                formatMoney(
                    (line.product.listPrice.toBigDecimalOrNull() ?: java.math.BigDecimal.ZERO) *
                        line.quantity.toBigDecimal()
                ),
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f)
            )
            IconButton(
                onClick = { actions.onSetQuantity(id, 0) },
                enabled = !locked,
                modifier = Modifier.size(Spacing.X5l)
            ) {
                MqIcon(
                    R.drawable.ic_lucide_trash_2,
                    contentDescription = stringResource(R.string.sale_remove_item, line.product.name)
                )
            }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    }
}
