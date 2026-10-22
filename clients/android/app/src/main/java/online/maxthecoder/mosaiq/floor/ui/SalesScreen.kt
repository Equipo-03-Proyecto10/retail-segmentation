package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.outbox.refusalAdvice
import online.maxthecoder.mosaiq.floor.session.ChosenStore

@Composable
fun SalesScreen(model: SalesViewModel, store: ChosenStore?, onPickStore: () -> Unit) {
    val customer by model.selectedCustomer.collectAsState()
    val sale by model.sale.collectAsState()
    val registration by model.registration.collectAsState()
    val history by model.history.collectAsState()
    val status by model.statusMessage.collectAsState()
    val statusError by model.statusError.collectAsState()
    LaunchedEffect(Unit) { model.loadChannels() }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
        item { Text("Customer", style = MaterialTheme.typography.titleLarge) }
        item { CustomerSearch(model) }
        item { Registration(model) }
        customer?.let { picked ->
            item { Text("Selected: ${picked.name} · ${picked.email ?: "no email"}") }
            item { Interests(model) }
        }
        item { Text("Sale", style = MaterialTheme.typography.titleLarge) }
        item {
            Text(store?.let { "Store: ${it.name} · channel: physical_store" } ?: "Check in at your store first.")
            OutlinedButton(onClick = onPickStore) { Text("Store & check-in") }
        }
        item { ProductSearch(model) }
        items(sale.lines, key = { it.product.productId }) { line ->
            Column {
                Text("${line.product.name} · ${line.product.listPrice} × ${line.quantity}")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(
                        onClick = { model.setQuantity(line.product.productId, line.quantity - 1) }
                    ) { Text("−") }
                    OutlinedButton(
                        onClick = { model.setQuantity(line.product.productId, line.quantity + 1) }
                    ) { Text("+") }
                }
            }
        }
        item {
            Button(
                enabled = customer != null && store != null && sale.lines.isNotEmpty() &&
                    sale.queuedRowId == null && !sale.confirming &&
                    registration.channels.any { it.name == "physical_store" },
                onClick = { model.confirm() }
            ) { Text(if (sale.confirming) "Saving on phone…" else "Confirm and queue sale") }
            if (sale.queuedRowId != null) {
                Text("Sale saved on this phone. Its progress appears below.")
                OutlinedButton(onClick = model::newSale) { Text("Start another sale") }
            }
        }
        item { Text("Sales on this phone", style = MaterialTheme.typography.titleMedium) }
        items(history, key = { it.id }) { row -> SaleProgress(model, row) }
        item {
            status?.let { Text(it) }
            statusError?.let { ErrorPanel(it) }
        }
    }
}

@Composable
private fun SaleProgress(model: SalesViewModel, row: OutboxRow) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        when (row.state) {
            RowState.PENDING -> Text("Sale ${row.clientId.take(8)} queued. Waiting for the connection.")
            RowState.REFUSED -> {
                ErrorPanel(
                    ApiError(row.status ?: 0, row.code ?: "refused", row.message ?: "Sale refused.", row.correlationId)
                )
                refusalAdvice(row.code)?.let { Text(it) }
            }
            RowState.DONE -> {
                Text(model.status(row) ?: "Sale recorded.")
                OutlinedButton(onClick = { model.refreshStatus(row) }) { Text("Check sale status") }
            }
        }
    }
}

@Composable
private fun CustomerSearch(model: SalesViewModel) {
    val state by model.customers.collectAsState()
    var query by rememberSaveable { mutableStateOf(state.query) }
    var email by rememberSaveable { mutableStateOf(state.email) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(
            query,
            { query = it },
            label = { Text("Name, email or phone") },
            modifier = Modifier.fillMaxWidth()
        )
        OutlinedTextField(
            email,
            { email = it },
            label = { Text("Exact email (optional)") },
            modifier = Modifier.fillMaxWidth()
        )
        Button(enabled = !state.loading, onClick = { model.searchCustomers(query, email) }) { Text("Search customers") }
        state.error?.let { ErrorPanel(it) }
        state.customers.forEach { customer ->
            TextButton(onClick = { model.selectCustomer(customer) }) {
                Text("${customer.name} · ${customer.email ?: customer.phone ?: "no contact"}")
            }
        }
        Text("Page ${state.page} of ${state.totalPages}")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(
                enabled = state.page > 1 && !state.loading,
                onClick = { model.searchCustomers(page = state.page - 1) }
            ) { Text("Previous") }
            OutlinedButton(
                enabled = state.page < state.totalPages && !state.loading,
                onClick = { model.searchCustomers(page = state.page + 1) }
            ) { Text("Next") }
        }
    }
}

@Composable
private fun Registration(model: SalesViewModel) {
    val state by model.registration.collectAsState()
    var name by rememberSaveable { mutableStateOf("") }
    var email by rememberSaveable { mutableStateOf("") }
    var phone by rememberSaveable { mutableStateOf("") }
    var open by rememberSaveable { mutableStateOf(false) }
    OutlinedButton(onClick = { open = !open }) { Text(if (open) "Hide registration" else "Register customer") }
    state.error?.let { ErrorPanel(it) }
    if (state.channels.isEmpty()) {
        OutlinedButton(enabled = !state.loading, onClick = model::loadChannels) { Text("Load channels") }
    }
    if (!open) return
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        RegistrationField("Name", "name", name, { name = it }, state.error)
        RegistrationField("Email", "email", email, { email = it }, state.error)
        RegistrationField("Phone", "phone", phone, { phone = it }, state.error)
        Text("Registration channel")
        state.channels.forEach { channel ->
            Row {
                RadioButton(
                    selected = state.selectedChannelId == channel.channelId,
                    onClick = { model.chooseRegistrationChannel(channel.channelId) }
                )
                Text(channel.name)
            }
        }
        state.error?.details?.filter { it.field == "registration_channel_id" }?.forEach { Text(it.code) }
        Button(
            enabled = name.isNotBlank() && state.selectedChannelId != null && !state.loading,
            onClick = { model.register(name, email, phone) }
        ) { Text("Register online") }
        if (state.timeoutAdvice) Text("The result is unknown. Search for this customer before registering again.")
        state.customer?.let { Text("Registered ${it.name}.") }
    }
}

@Composable
private fun RegistrationField(label: String, field: String, value: String, change: (String) -> Unit, error: ApiError?) {
    val details = error?.details.orEmpty().filter { it.field == field }
    OutlinedTextField(
        value,
        change,
        label = { Text(label) },
        isError = details.isNotEmpty(),
        supportingText = { details.forEach { Text(it.code) } },
        modifier = Modifier.fillMaxWidth()
    )
}

@Composable
private fun Interests(model: SalesViewModel) {
    val state by model.interests.collectAsState()
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("Interests", style = MaterialTheme.typography.titleMedium)
        state.categories.forEach { category ->
            Row {
                Checkbox(
                    checked = category.categoryId in state.selected,
                    enabled = state.ready && !state.loading,
                    onCheckedChange = { model.toggleInterest(category.categoryId) }
                )
                Text(category.name)
            }
        }
        if (!state.ready) {
            OutlinedButton(enabled = !state.loading, onClick = model::loadInterests) { Text("Load interests") }
        }
        Button(enabled = state.ready && !state.loading, onClick = model::saveInterests) { Text("Save interests") }
        if (state.saved) Text("Interests saved.")
        state.error?.let { ErrorPanel(it) }
    }
}

@Composable
private fun ProductSearch(model: SalesViewModel) {
    val state by model.sale.collectAsState()
    var query by rememberSaveable { mutableStateOf(state.query) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Search products, or use Scan a product and tap Add to sale.")
        OutlinedTextField(query, { query = it }, label = { Text("Product") }, modifier = Modifier.fillMaxWidth())
        Button(enabled = query.isNotBlank() && !state.loading, onClick = {
            model.searchProducts(query)
        }) { Text("Search products") }
        state.error?.let { ErrorPanel(it) }
        state.products.forEach { product ->
            TextButton(enabled = product.isActive, onClick = { model.addProduct(product) }) {
                Text("Add ${product.name} · ${product.listPrice}")
            }
        }
    }
}
