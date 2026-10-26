package online.maxthecoder.mosaiq.floor.ui.checkin

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.ui.ErrorPanel
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

@Composable
fun StorePicker(model: FloorViewModel, onPicked: () -> Unit = {}) {
    val state by model.stores.collectAsStateWithLifecycle()
    // Saved, so a rotation does not clear what was typed.
    var city by rememberSaveable { mutableStateOf(state.city) }
    LaunchedEffect(Unit) { if (state.stores.isEmpty()) model.searchStores() }
    Column(modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(Spacing.Md)) {
        Text("Pick your store", style = MaterialTheme.typography.titleMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
            OutlinedTextField(value = city, onValueChange = { city = it }, label = { Text("City") }, singleLine = true)
            Button(onClick = { model.searchStores(city) }) { Text("Search") }
        }
        state.error?.let { ErrorPanel(it) }
        // Weighted, so a long list scrolls itself and the paging row below stays on screen.
        LazyColumn(modifier = Modifier.weight(1f).fillMaxWidth()) {
            items(state.stores, key = { it.storeId }) { store ->
                TextButton(onClick = {
                    model.choose(store)
                    onPicked()
                }) { Text("${store.name} · ${store.city}, ${store.state}") }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
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
}
