package online.maxthecoder.mosaiq.floor.ui

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.outbox.refusalAdvice
import online.maxthecoder.mosaiq.floor.session.ChosenStore

private enum class Tab { CHECK_IN, SCAN, SALE, OUTBOX }

/** The floor process once signed in: who, the store and its check-in, the outbox. */
@Composable
fun FloorHome(
    signedIn: SessionState.SignedIn,
    model: FloorViewModel,
    scan: ScanViewModel,
    sales: SalesViewModel,
    insights: CustomerInsightsViewModel,
    onSignOut: () -> Unit
) {
    var tab by remember { mutableStateOf(Tab.CHECK_IN) }
    val chosen by model.chosen.collectAsState()
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("${signedIn.name} · ${signedIn.role}", style = MaterialTheme.typography.titleMedium)
        Row(
            modifier = Modifier.horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            OutlinedButton(onClick = { tab = Tab.CHECK_IN }) { Text("Store & check-in") }
            OutlinedButton(onClick = { tab = Tab.SCAN }) { Text("Scan a product") }
            OutlinedButton(onClick = { tab = Tab.SALE }) { Text("Customer & sale") }
            OutlinedButton(onClick = { tab = Tab.OUTBOX }) { Text("Outbox") }
            SignOutButton(model, onSignOut)
        }
        when (tab) {
            Tab.CHECK_IN -> StoreAndCheckIn(model)
            Tab.SCAN ->
                ScanScreen(
                    scan,
                    chosen,
                    onPickStore = { tab = Tab.CHECK_IN },
                    onAddProduct = sales::addProduct
                )
            Tab.SALE ->
                SalesScreen(
                    sales,
                    chosen,
                    onPickStore = { tab = Tab.CHECK_IN },
                    insights = insights,
                    onOpenProduct = { productId ->
                        scan.showProduct(productId)
                        tab = Tab.SCAN
                    }
                )
            Tab.OUTBOX -> OutboxList(model)
        }
    }
}

/** Signing out with writes still queued warns first: they wait for this user. */
@Composable
fun SignOutButton(model: FloorViewModel, onSignOut: () -> Unit) {
    val scope = rememberCoroutineScope()
    var pending by remember { mutableIntStateOf(0) }
    TextButton(onClick = {
        scope.launch {
            val count = model.pendingForCurrentUser()
            // Room answers on its own thread; the dialog belongs to the main one.
            withContext(Dispatchers.Main.immediate) {
                pending = count
                if (count == 0) onSignOut()
            }
        }
    }) { Text("Sign out") }
    if (pending > 0) {
        AlertDialog(
            onDismissRequest = { pending = 0 },
            title = { Text("Writes are still waiting") },
            text = {
                Text(
                    "$pending floor write(s) have not been sent yet. They stay on this phone and are " +
                        "sent only when you sign in again."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    pending = 0
                    onSignOut()
                }) { Text("Sign out anyway") }
            },
            dismissButton = { TextButton(onClick = { pending = 0 }) { Text("Stay signed in") } }
        )
    }
}

@Composable
private fun StoreAndCheckIn(model: FloorViewModel) {
    val chosen by model.chosen.collectAsState()
    val store = chosen
    if (store == null) {
        StorePicker(model)
        return
    }
    var picking by remember { mutableStateOf(false) }
    if (picking) {
        StorePicker(model, onPicked = { picking = false })
        return
    }
    Text("Store: ${store.name}", style = MaterialTheme.typography.titleMedium)
    OutlinedButton(onClick = { picking = true }) { Text("Change store") }
    CheckInPanel(model, store)
}

@Composable
private fun StorePicker(model: FloorViewModel, onPicked: () -> Unit = {}) {
    val state by model.stores.collectAsState()
    var city by remember { mutableStateOf(state.city) }
    LaunchedEffect(Unit) { if (state.stores.isEmpty()) model.searchStores() }
    Text("Pick your store", style = MaterialTheme.typography.titleMedium)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(value = city, onValueChange = { city = it }, label = { Text("City") }, singleLine = true)
        Button(onClick = { model.searchStores(city) }) { Text("Search") }
    }
    state.error?.let { ErrorPanel(it) }
    LazyColumn(modifier = Modifier.fillMaxWidth()) {
        items(state.stores, key = { it.storeId }) { store ->
            TextButton(onClick = {
                model.choose(store)
                onPicked()
            }) { Text("${store.name} · ${store.city}, ${store.state}") }
        }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(enabled = state.page > 1, onClick = { model.searchStores(page = state.page - 1) }) {
            Text("Previous")
        }
        Text("Page ${state.page} of ${state.totalPages}")
        OutlinedButton(
            enabled = state.page < state.totalPages,
            onClick = { model.searchStores(page = state.page + 1) }
        ) { Text("Next") }
    }
}

@Composable
private fun CheckInPanel(model: FloorViewModel, store: ChosenStore) {
    val state by model.checkIn.collectAsState()
    val row by model.lastCheckIn.collectAsState()
    val permissions =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
            val mode =
                when {
                    granted[Manifest.permission.ACCESS_FINE_LOCATION] == true -> LocationMode.PRECISE
                    granted[Manifest.permission.ACCESS_COARSE_LOCATION] == true -> LocationMode.APPROXIMATE
                    else -> LocationMode.DENIED
                }
            model.checkInAt(store, mode)
        }
    Button(
        enabled = state != CheckInState.Locating,
        onClick = {
            permissions.launch(
                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)
            )
        }
    ) { Text("Check in") }
    when (val current = state) {
        CheckInState.Idle -> Unit
        CheckInState.Locating -> Text("Finding your position…")
        is CheckInState.Queued -> {
            current.note?.let { Text(it) }
            val answer = FloorViewModel.resultOf(row)
            when {
                answer != null -> Text(answer, style = MaterialTheme.typography.bodyLarge)
                row?.state == RowState.REFUSED -> ErrorPanel(row!!.asError())
                else -> Text("Check-in queued. It is sent as soon as the network allows.")
            }
        }
    }
}

@Composable
private fun OutboxList(model: FloorViewModel) {
    val rows by model.open.collectAsState()
    val userId by model.userId.collectAsState()
    if (rows.isEmpty()) {
        Text("Nothing is waiting, and nothing was refused.")
        return
    }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        items(rows, key = { it.id }) { row ->
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("${row.operation} · ${row.clientId.take(8)}", style = MaterialTheme.typography.titleSmall)
                when {
                    row.state == RowState.REFUSED -> {
                        ErrorPanel(row.asError())
                        refusalAdvice(row.code)?.let { Text(it) }
                        OutlinedButton(onClick = { model.dismiss(row) }) { Text("Dismiss") }
                    }
                    row.userId != userId -> Text("Waiting for the user who made it to sign in again.")
                    else -> Text("Waiting to be sent (${row.attempts} attempt(s) so far).")
                }
            }
        }
    }
}

private fun OutboxRow.asError(): ApiError = ApiError(
    status = status ?: 0,
    code = code ?: "refused",
    message = message ?: "The service refused this write.",
    correlationId = correlationId
)
