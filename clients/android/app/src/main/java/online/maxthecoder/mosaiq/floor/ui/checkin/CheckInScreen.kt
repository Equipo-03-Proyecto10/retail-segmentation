package online.maxthecoder.mosaiq.floor.ui.checkin

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.CheckInState
import online.maxthecoder.mosaiq.floor.ui.ErrorPanel
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.LocationMode
import online.maxthecoder.mosaiq.floor.ui.outbox.asError
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * The Check-in section. Whether the store picker is showing is the shell's
 * location (`picking`), so it survives rotation and Back closes it.
 */
@Composable
fun StoreAndCheckIn(model: FloorViewModel, picking: Boolean, onChangeStore: () -> Unit, onPicked: () -> Unit) {
    val chosen by model.chosen.collectAsStateWithLifecycle()
    val store = chosen
    if (store == null) {
        StorePicker(model, onPicked = onPicked)
        return
    }
    if (picking) {
        StorePicker(model, onPicked = onPicked)
        return
    }
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(Spacing.Md)
    ) {
        Text("Store: ${store.name}", style = MaterialTheme.typography.titleMedium)
        OutlinedButton(onClick = onChangeStore) { Text("Change store") }
        CheckInPanel(model, store)
    }
}

@Composable
fun CheckInPanel(model: FloorViewModel, store: ChosenStore) {
    val state by model.checkIn.collectAsStateWithLifecycle()
    val row by model.lastCheckIn.collectAsStateWithLifecycle()
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
            val refused = row?.takeIf { it.state == RowState.REFUSED }
            when {
                answer != null -> Text(answer, style = MaterialTheme.typography.bodyLarge)
                refused != null -> ErrorPanel(refused.asError())
                else -> Text("Check-in queued. It is sent as soon as the network allows.")
            }
        }
    }
}
