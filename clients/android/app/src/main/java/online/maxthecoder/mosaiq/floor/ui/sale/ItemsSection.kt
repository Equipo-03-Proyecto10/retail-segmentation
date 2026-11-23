package online.maxthecoder.mosaiq.floor.ui.sale

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.ui.DiscountState
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

/**
 * Section 2: search products to add, and the cart as rows with a quantity stepper. [fixLine] is the product
 * whose line a refusal named; once the cart is open again that line scrolls into view, and [onLineShown] says so.
 */
@Composable
fun ItemsSection(ui: SaleUi, actions: SaleActions, fixLine: Int? = null, onLineShown: () -> Unit = {}) {
    val sale = ui.sale
    var query by rememberSaveable { mutableStateOf(sale.query) }
    var searched by rememberSaveable { mutableStateOf(sale.products.isNotEmpty()) }
    val locked = sale.confirming || sale.queuedRowId != null || sale.reviewing
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
                    enabled = product.isActive && !locked && ui.store != null,
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
        Text(
            stringResource(R.string.sale_cart_title),
            style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.semantics { heading() }
        )
        if (sale.lines.isEmpty()) {
            MqEmptyState(
                icon = R.drawable.ic_lucide_shopping_cart,
                title = stringResource(R.string.sale_cart_empty_title),
                message = stringResource(R.string.sale_cart_empty_help),
                modifier = Modifier.testTag("cart-empty")
            )
        }
        if (sale.lines.isNotEmpty()) {
            when {
                sale.discounts.loading -> MqLoading(stringResource(R.string.sale_promotions_loading))
                sale.discounts.error != null ->
                    MqErrorPanel(
                        sale.discounts.error,
                        onRetry = actions.onLoadPromotions.takeIf { sale.discounts.error.retryable }
                    )
            }
        }
        sale.lines.forEach { line ->
            val fix = !locked && line.product.productId == fixLine
            CartLine(line, locked, sale.discounts, actions, fix, onLineShown)
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun CartLine(
    line: SaleLine,
    locked: Boolean,
    discounts: DiscountState,
    actions: SaleActions,
    fix: Boolean = false,
    onShown: () -> Unit = {}
) {
    val id = line.product.productId
    val cap = discounts.maxDiscountPercent
    val options = if (discounts.loaded) applicablePromotions(discounts) else emptyList()
    val requester = remember { BringIntoViewRequester() }
    LaunchedEffect(fix) {
        if (fix) {
            requester.bringIntoView()
            onShown()
        }
    }
    Column(
        Modifier.fillMaxWidth().bringIntoViewRequester(requester).testTag("cart-line-$id"),
        verticalArrangement = Arrangement.spacedBy(Spacing.Sm)
    ) {
        Column(Modifier.semantics(mergeDescendants = true) {}) {
            Text(line.product.name, style = MaterialTheme.typography.bodyLarge)
            Text(
                if (line.promotion == null) {
                    stringResource(R.string.sale_unit_price, formatMoney(line.product.listPrice))
                } else {
                    stringResource(
                        R.string.sale_unit_price_discounted,
                        formatMoney(chargedPrice(line, cap)),
                        formatMoney(line.product.listPrice)
                    )
                },
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Spacing.Md)) {
            MqQuantityStepper(
                value = line.quantity,
                onDecrease = { actions.onSetQuantity(id, line.quantity - 1) },
                onIncrease = { actions.onSetQuantity(id, line.quantity + 1) },
                modifier = Modifier.testTag("stepper-$id"),
                itemName = line.product.name
            )
            Text(
                formatMoney(lineTotal(line, cap)),
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f).testTag("line-total-$id")
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
        if (options.isNotEmpty() || line.promotion != null) {
            PromotionChoice(line, options, cap, locked, actions)
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    }
}

/** One promotion per line, named by the seller from what campaigns lists here, or none. */
@Composable
private fun PromotionChoice(
    line: SaleLine,
    options: List<Promotion>,
    cap: String?,
    locked: Boolean,
    actions: SaleActions
) {
    val id = line.product.productId
    Column(Modifier.selectableGroup().testTag("promotions-$id")) {
        Text(
            stringResource(R.string.sale_promotion_title),
            style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.semantics { heading() }
        )
        PromotionRow(
            title = stringResource(R.string.sale_promotion_none),
            detail = null,
            selected = line.promotion == null,
            enabled = !locked,
            onClick = { actions.onChoosePromotion(id, null) }
        )
        // A promotion no longer listed stays visible while it is chosen, so the seller can take it off.
        (options + listOfNotNull(line.promotion?.takeIf { chosen -> options.none { it == chosen } })).forEach {
            val percent = allowedPercent(it, cap).stripTrailingZeros().toPlainString()
            PromotionRow(
                title = it.name,
                detail = if (cappedByStore(it, cap)) {
                    stringResource(R.string.sale_percent_off_capped, percent)
                } else {
                    stringResource(R.string.sale_percent_off, percent)
                },
                selected = line.promotion?.promotionId == it.promotionId,
                enabled = !locked && it in options,
                onClick = { actions.onChoosePromotion(id, it.promotionId) }
            )
        }
    }
}

@Composable
private fun PromotionRow(title: String, detail: String?, selected: Boolean, enabled: Boolean, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = Spacing.X5l).selectable(
            selected = selected,
            enabled = enabled,
            role = Role.RadioButton,
            onClick = onClick
        ),
        horizontalArrangement = Arrangement.spacedBy(Spacing.Sm),
        verticalAlignment = Alignment.CenterVertically
    ) {
        RadioButton(selected = selected, onClick = null, enabled = enabled)
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            detail?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}
