package online.maxthecoder.mosaiq.floor.ui.checkin

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.tooling.preview.Preview
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Store
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.StoresState
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqEmptyState
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqPager
import online.maxthecoder.mosaiq.floor.ui.components.MqTextField
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

@Composable
fun StorePicker(model: FloorViewModel, onPicked: () -> Unit = {}) {
    val state by model.stores.collectAsStateWithLifecycle()
    // Saved, so a rotation does not clear what was typed.
    var city by rememberSaveable { mutableStateOf(state.city) }
    LaunchedEffect(Unit) { if (state.stores.isEmpty()) model.searchStores() }
    StorePickerContent(
        state = state,
        city = city,
        onCity = { city = it },
        onSearch = { model.searchStores(city) },
        onRetry = { model.searchStores(state.city, state.page) },
        onPage = { model.searchStores(page = it) },
        onPick = {
            model.choose(it)
            onPicked()
        }
    )
}

/** The store picker: stateless, so previews and tests can drive loading, empty, error and results. */
@Composable
fun StorePickerContent(
    state: StoresState,
    city: String,
    onCity: (String) -> Unit,
    onSearch: () -> Unit,
    onRetry: () -> Unit,
    onPage: (Int) -> Unit,
    onPick: (Store) -> Unit
) {
    val search = { if (!state.loading) onSearch() }
    Column(modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(Spacing.Md)) {
        Text(
            stringResource(R.string.picker_title),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm), verticalAlignment = Alignment.Top) {
            MqTextField(
                value = city,
                onValueChange = onCity,
                label = stringResource(R.string.picker_city),
                modifier = Modifier.weight(1f),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { search() })
            )
            MqButton(
                stringResource(R.string.picker_search),
                search,
                enabled = !state.loading,
                variant = MqButtonVariant.Secondary,
                leadingIcon = R.drawable.ic_lucide_search,
                modifier = Modifier.padding(top = Spacing.Xs)
            )
        }
        // Weighted, so a long list scrolls itself and the paging row below stays on screen.
        Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
            when {
                state.loading -> MqLoading(stringResource(R.string.picker_searching), fillArea = true)
                state.error != null -> MqErrorPanel(state.error, onRetry = onRetry)
                state.stores.isEmpty() ->
                    MqEmptyState(
                        icon = R.drawable.ic_lucide_store,
                        title = stringResource(R.string.picker_empty_title),
                        message = stringResource(R.string.picker_empty_message)
                    )
                else ->
                    LazyColumn(modifier = Modifier.fillMaxSize()) {
                        items(state.stores, key = { it.storeId }) { store ->
                            MqListRow(
                                headline = store.name,
                                supporting = "${store.city}, ${store.state}",
                                chevron = true,
                                divider = true,
                                onClick = { onPick(store) }
                            )
                        }
                    }
            }
        }
        MqPager(
            page = state.page,
            pageCount = state.totalPages,
            onPrevious = { if (!state.loading) onPage(state.page - 1) },
            onNext = { if (!state.loading) onPage(state.page + 1) }
        )
    }
}

private const val PREVIEW_WIDTH = 375
private const val PREVIEW_HEIGHT = 640

private val SampleStores =
    listOf(
        Store(1, "Centro", "Monterrey", "NL", true),
        Store(2, "Valle", "Monterrey", "NL", true),
        Store(3, "Cumbres", "Monterrey", "NL", true)
    )

@Composable
private fun PickerPreview(dark: Boolean, state: StoresState) = MosaiqTheme(darkTheme = dark) {
    Surface {
        Column(Modifier.fillMaxSize().padding(Spacing.Lg)) {
            StorePickerContent(state, state.city, {}, {}, {}, {}, {})
        }
    }
}

@Preview(name = "Picker results, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun ResultsLight() = PickerPreview(false, StoresState(stores = SampleStores, totalPages = 2))

@Preview(name = "Picker empty, dark", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun EmptyDark() = PickerPreview(true, StoresState(city = "Atlantis"))

@Preview(name = "Picker error, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun ErrorLight() = PickerPreview(false, StoresState(error = ApiError.unreachable(null)))
