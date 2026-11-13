package online.maxthecoder.mosaiq.floor.ui.sale

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.ui.InterestsState
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqEmptyState
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqPager
import online.maxthecoder.mosaiq.floor.ui.components.MqSection
import online.maxthecoder.mosaiq.floor.ui.components.MqTextField
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** Past this font scale a row of text beside a button stacks instead. */
private const val LARGE_FONT_SCALE = 1.3f

/** Section 1: find or register a customer, or show the chosen one with their segment. */
@Composable
fun CustomerSection(ui: SaleUi, actions: SaleActions, onNewCustomer: () -> Unit) {
    var changing by rememberSaveable { mutableStateOf(false) }
    val chosen = ui.customer
    LaunchedEffect(chosen?.customerId) { changing = false }
    if (chosen != null && !changing) {
        ChosenCustomer(chosen, ui, actions, onChange = { changing = true })
    } else {
        CustomerSearch(
            ui,
            actions,
            onNewCustomer,
            onCancel = if (chosen != null) ({ changing = false }) else null,
            onPicked = { changing = false }
        )
    }
}

@Composable
private fun ChosenCustomer(customer: Customer, ui: SaleUi, actions: SaleActions, onChange: () -> Unit) {
    var interestsOpen by rememberSaveable { mutableStateOf(false) }
    MqSection(stringResource(R.string.sale_customer_title)) {
        val details: @Composable (Modifier) -> Unit = { modifier ->
            Column(
                modifier.semantics(mergeDescendants = true) {},
                verticalArrangement = Arrangement.spacedBy(Spacing.Xs)
            ) {
                Text(
                    customer.name,
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.testTag("chosen-customer")
                )
                Text(
                    customer.email ?: customer.phone ?: stringResource(R.string.sale_no_contact),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
        val change = stringResource(R.string.sale_change)
        // At large font sizes the email would wrap mid-word beside the button, so the button drops below.
        if (LocalDensity.current.fontScale > LARGE_FONT_SCALE) {
            Column(verticalArrangement = Arrangement.spacedBy(Spacing.Md)) {
                details(Modifier)
                MqButton(change, onChange, variant = MqButtonVariant.Secondary)
            }
        } else {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(Spacing.Md)
            ) {
                details(Modifier.weight(1f))
                MqButton(change, onChange, variant = MqButtonVariant.Secondary)
            }
        }
        MqButton(
            stringResource(if (interestsOpen) R.string.sale_interests_hide else R.string.sale_interests_show),
            { interestsOpen = !interestsOpen },
            variant = MqButtonVariant.Quiet
        )
        if (interestsOpen) Interests(ui.interests, actions)
    }
}

@Composable
private fun Interests(state: InterestsState, actions: SaleActions) {
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.Xs)) {
        when {
            state.loading && !state.ready -> MqLoading(stringResource(R.string.sale_interests_loading))
            state.error != null -> MqErrorPanel(state.error, onRetry = actions.onLoadInterests)
            !state.ready -> MqButton(
                stringResource(R.string.sale_interests_load),
                actions.onLoadInterests,
                variant = MqButtonVariant.Secondary
            )
        }
        state.categories.forEach { category ->
            val checked = category.categoryId in state.selected
            Row(
                Modifier.fillMaxWidth().heightIn(min = Spacing.X5l).toggleable(
                    value = checked,
                    enabled = state.ready && !state.loading,
                    role = Role.Checkbox,
                    onValueChange = { actions.onToggleInterest(category.categoryId) }
                ),
                horizontalArrangement = Arrangement.spacedBy(Spacing.Sm),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Checkbox(checked = checked, onCheckedChange = null, enabled = state.ready && !state.loading)
                Text(category.name, style = MaterialTheme.typography.bodyLarge)
            }
        }
        if (state.ready) {
            MqButton(
                stringResource(R.string.sale_interests_save),
                actions.onSaveInterests,
                variant = MqButtonVariant.Secondary,
                loading = state.loading
            )
            if (state.saved) {
                Text(
                    stringResource(R.string.sale_interests_saved),
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }
                )
            }
        }
    }
}

@Composable
private fun CustomerSearch(
    ui: SaleUi,
    actions: SaleActions,
    onNewCustomer: () -> Unit,
    onCancel: (() -> Unit)?,
    onPicked: () -> Unit
) {
    val state = ui.customers
    var query by rememberSaveable { mutableStateOf(state.query) }
    var email by rememberSaveable { mutableStateOf(state.email) }
    var searched by rememberSaveable { mutableStateOf(state.customers.isNotEmpty()) }
    val search = {
        searched = true
        actions.onSearchCustomers(query, email)
    }
    MqSection(stringResource(R.string.sale_customer_title)) {
        MqTextField(
            query,
            { query = it },
            stringResource(R.string.sale_search_label),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { search() }),
            modifier = Modifier.testTag("customer-query")
        )
        MqTextField(
            email,
            { email = it },
            stringResource(R.string.sale_search_email_label),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { search() }),
            modifier = Modifier.testTag("customer-email")
        )
        MqButton(
            stringResource(R.string.sale_search_button),
            search,
            enabled = !state.loading,
            fullWidth = true,
            variant = MqButtonVariant.Secondary
        )
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
            MqButton(
                stringResource(R.string.sale_new_customer),
                onNewCustomer,
                variant = MqButtonVariant.Quiet,
                leadingIcon = R.drawable.ic_lucide_plus
            )
            if (onCancel != null) {
                MqButton(
                    stringResource(R.string.sale_cancel),
                    onCancel,
                    variant = MqButtonVariant.Quiet
                )
            }
        }
        when {
            state.loading -> MqLoading(stringResource(R.string.sale_search_loading))
            state.error != null -> MqErrorPanel(state.error, onRetry = search)
            state.customers.isEmpty() && searched ->
                MqEmptyState(
                    icon = R.drawable.ic_lucide_user_search,
                    title = stringResource(R.string.sale_search_empty_title),
                    message = stringResource(R.string.sale_search_empty_help),
                    modifier = Modifier.testTag("customers-empty")
                )
        }
        if (!state.loading) {
            state.customers.forEach { customer ->
                MqListRow(
                    headline = customer.name,
                    supporting = customer.email ?: customer.phone ?: stringResource(R.string.sale_no_contact),
                    onClick = {
                        actions.onSelectCustomer(customer)
                        onPicked()
                    },
                    chevron = true,
                    divider = true
                )
            }
            if (state.totalPages > 1) {
                MqPager(
                    state.page,
                    state.totalPages,
                    onPrevious = { actions.onCustomerPage(state.page - 1) },
                    onNext = { actions.onCustomerPage(state.page + 1) }
                )
            }
        }
    }
}
