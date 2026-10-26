package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.Channel
import online.maxthecoder.mosaiq.floor.net.Customer
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqCard
import online.maxthecoder.mosaiq.floor.ui.components.MqSnackbarHost
import online.maxthecoder.mosaiq.floor.ui.sale.CustomerSection
import online.maxthecoder.mosaiq.floor.ui.sale.HistorySection
import online.maxthecoder.mosaiq.floor.ui.sale.ItemsSection
import online.maxthecoder.mosaiq.floor.ui.sale.RegistrationFields
import online.maxthecoder.mosaiq.floor.ui.sale.RegistrationSheet
import online.maxthecoder.mosaiq.floor.ui.sale.SaleActions
import online.maxthecoder.mosaiq.floor.ui.sale.SaleBar
import online.maxthecoder.mosaiq.floor.ui.sale.SaleUi
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * The sale flow on one scrolling screen: the customer, the items, and a bar with the
 * total and "Record sale" pinned at the bottom. Insights load once per chosen customer,
 * from here and not from a lazy item, so scrolling never reloads them.
 */
@Composable
fun SalesScreen(
    model: SalesViewModel,
    store: ChosenStore?,
    onPickStore: () -> Unit,
    insights: CustomerInsightsViewModel,
    onOpenProduct: (Int) -> Unit
) {
    val customer by model.selectedCustomer.collectAsStateWithLifecycle()
    val customers by model.customers.collectAsStateWithLifecycle()
    val sale by model.sale.collectAsStateWithLifecycle()
    val registration by model.registration.collectAsStateWithLifecycle()
    val interests by model.interests.collectAsStateWithLifecycle()
    val history by model.history.collectAsStateWithLifecycle()
    val status by model.statusMessage.collectAsStateWithLifecycle()
    val statusError by model.statusError.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val saved = stringResource(R.string.sale_saved)
    val customerId = customer?.customerId

    LaunchedEffect(Unit) { model.loadChannels() }
    LaunchedEffect(customerId) {
        if (customerId != null && insights.state.value.customerId != customerId) insights.load(customerId)
    }
    LaunchedEffect(sale.queuedRowId) {
        if (sale.queuedRowId != null) snackbar.showSnackbar(saved)
    }

    val ui =
        SaleUi(
            customer = customer,
            customers = customers,
            registration = registration,
            interests = interests,
            sale = sale,
            history = history,
            store = store,
            statusMessage = status,
            statusError = statusError
        )
    val actions =
        SaleActions(
            onSearchCustomers = { query, email -> model.searchCustomers(query, email) },
            onCustomerPage = { page -> model.searchCustomers(page = page) },
            onSelectCustomer = model::selectCustomer,
            onRegister = model::register,
            onChooseChannel = model::chooseRegistrationChannel,
            onLoadChannels = model::loadChannels,
            onToggleInterest = model::toggleInterest,
            onSaveInterests = model::saveInterests,
            onLoadInterests = model::loadInterests,
            onSearchProducts = model::searchProducts,
            onAddProduct = model::addProduct,
            onSetQuantity = model::setQuantity,
            onConfirm = { model.confirm() },
            onNewSale = model::newSale,
            onRefreshStatus = model::refreshStatus,
            onPickStore = onPickStore,
            rowStatus = model::status
        )
    SalesContent(ui, actions, snackbar) { CustomerInsights(insights, onOpenProduct) }
}

/** The stateless sale screen; previews and tests drive it with any [SaleUi]. */
@Composable
fun SalesContent(ui: SaleUi, actions: SaleActions, snackbar: SnackbarHostState, insights: @Composable () -> Unit) {
    var registering by rememberSaveable { mutableStateOf(false) }
    var name by rememberSaveable { mutableStateOf("") }
    var email by rememberSaveable { mutableStateOf("") }
    var phone by rememberSaveable { mutableStateOf("") }
    val registered = ui.registration.customer?.customerId
    // A successful registration selects the new customer: close the sheet and clear the form.
    LaunchedEffect(registered) {
        if (registered != null) {
            registering = false
            name = ""
            email = ""
            phone = ""
        }
    }
    Column(Modifier.fillMaxSize()) {
        Column(
            Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(bottom = Spacing.Lg),
            verticalArrangement = Arrangement.spacedBy(Spacing.Xxl)
        ) {
            StoreLine(ui.store, actions.onPickStore)
            CustomerSection(ui, actions, onNewCustomer = { registering = true })
            if (ui.customer != null) insights()
            ItemsSection(ui, actions)
            HistorySection(ui, actions)
        }
        MqSnackbarHost(snackbar)
        SaleBar(ui, actions)
    }
    if (registering) {
        RegistrationSheet(
            fields = RegistrationFields(name, email, phone),
            onFields = {
                name = it.name
                email = it.email
                phone = it.phone
            },
            registration = ui.registration,
            onChooseChannel = actions.onChooseChannel,
            onLoadChannels = actions.onLoadChannels,
            onSubmit = { actions.onRegister(name, email, phone) },
            onDismiss = { registering = false }
        )
    }
}

@Composable
private fun StoreLine(store: ChosenStore?, onPickStore: () -> Unit) {
    if (store == null) {
        MqCard {
            Text(stringResource(R.string.sale_store_missing), style = MaterialTheme.typography.bodyLarge)
            MqButton(stringResource(R.string.sale_store_action), onPickStore, variant = MqButtonVariant.Secondary)
        }
    } else {
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Md), verticalAlignment = Alignment.CenterVertically) {
            Text(
                stringResource(R.string.sale_store_line, store.name),
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.weight(1f)
            )
            MqBadge(stringResource(R.string.sale_channel_in_store))
        }
    }
}

private val previewStore = ChosenStore(1, "Centro")
private val previewCustomer = Customer("c1", "Ana Torres", "ana@example.com", null, 1, "2025-01-01")
private val previewProduct = Product(1, "SKU1", "Trail socks", 3, "12.5", true)
private val previewChannels = listOf(Channel(1, "physical_store"), Channel(2, "online"))

@Composable
private fun SalePreview(dark: Boolean, ui: SaleUi) = MosaiqTheme(darkTheme = dark) {
    Column(Modifier.fillMaxSize().padding(Spacing.Lg)) {
        SalesContent(ui, SaleActions(), SnackbarHostState()) {}
    }
}

private val previewFilled =
    SaleUi(
        customer = previewCustomer,
        store = previewStore,
        registration = RegistrationState(channels = previewChannels, selectedChannelId = 1),
        sale = SaleBuilderState(lines = listOf(SaleLine(previewProduct, 2)))
    )

@Preview(name = "Sale, light", widthDp = 375, heightDp = 900, showBackground = true)
@Composable
private fun SaleLight() = SalePreview(false, previewFilled)

@Preview(name = "Sale, dark", widthDp = 375, heightDp = 900, showBackground = true)
@Composable
private fun SaleDark() = SalePreview(true, previewFilled)

@Preview(name = "Sale empty, light", widthDp = 375, heightDp = 900, showBackground = true)
@Composable
private fun SaleEmptyLight() = SalePreview(false, SaleUi(store = previewStore))
