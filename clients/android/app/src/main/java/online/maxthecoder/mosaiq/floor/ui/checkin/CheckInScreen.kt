package online.maxthecoder.mosaiq.floor.ui.checkin

import android.Manifest
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.time.ZoneId
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.CheckInState
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.LocationMode
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqCard
import online.maxthecoder.mosaiq.floor.ui.components.MqEmptyState
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqIcon
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.formatClock
import online.maxthecoder.mosaiq.floor.ui.outbox.asError
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * The Check-in section. Whether the store picker is showing is the shell's
 * location (`picking`), so it survives rotation and Back closes it.
 */
@Composable
fun StoreAndCheckIn(model: FloorViewModel, picking: Boolean, onChangeStore: () -> Unit, onPicked: () -> Unit) {
    val chosen by model.chosen.collectAsStateWithLifecycle()
    val store = chosen
    if (picking) {
        StorePicker(model, onPicked = onPicked)
        return
    }
    if (store == null) {
        MqEmptyState(
            icon = R.drawable.ic_lucide_store,
            title = stringResource(R.string.checkin_none_title),
            message = stringResource(R.string.checkin_none_message),
            actionLabel = stringResource(R.string.checkin_none_action),
            onAction = onChangeStore
        )
        return
    }
    CheckInPanel(model, store, onChangeStore)
}

/** What the status card says about the last check-in. */
sealed interface CheckInStatus {
    data object NotCheckedIn : CheckInStatus

    /** Finding the position, then saving the check-in. */
    data object Sending : CheckInStatus

    /** Saved on the phone; it goes out when the network allows. */
    data object Waiting : CheckInStatus

    /** The server took it; [time] is the clock time of the check-in. */
    data class Done(val time: String) : CheckInStatus

    /** The server refused it. */
    data class NeedsAttention(val error: ApiError) : CheckInStatus
}

@Composable
fun CheckInPanel(model: FloorViewModel, store: ChosenStore, onChangeStore: () -> Unit = {}) {
    val state by model.checkIn.collectAsStateWithLifecycle()
    val row by model.lastCheckIn.collectAsStateWithLifecycle()
    val stores by model.stores.collectAsStateWithLifecycle()
    var denied by rememberSaveable { mutableStateOf(false) }
    val context = LocalContext.current
    val permissions =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
            val mode =
                when {
                    granted[Manifest.permission.ACCESS_FINE_LOCATION] == true -> LocationMode.PRECISE
                    granted[Manifest.permission.ACCESS_COARSE_LOCATION] == true -> LocationMode.APPROXIMATE
                    else -> LocationMode.DENIED
                }
            denied = mode == LocationMode.DENIED
            model.checkInAt(store, mode)
        }
    val current = state
    val refused = row?.takeIf { it.state == RowState.REFUSED }
    val status =
        when {
            current == CheckInState.Locating -> CheckInStatus.Sending
            current is CheckInState.Queued && refused != null -> CheckInStatus.NeedsAttention(refused.asError())
            current is CheckInState.Queued && row?.state == RowState.DONE ->
                CheckInStatus.Done(clockTime(row?.createdAt ?: 0L))
            current is CheckInState.Queued -> CheckInStatus.Waiting
            else -> CheckInStatus.NotCheckedIn
        }
    val place = stores.stores.firstOrNull { it.storeId == store.storeId }
    CheckInContent(
        storeName = store.name,
        place = place?.let { "${it.city}, ${it.state}" },
        status = status,
        result = FloorViewModel.resultOf(row),
        note = (current as? CheckInState.Queued)?.note,
        locationDenied = denied,
        onCheckIn = {
            permissions.launch(
                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)
            )
        },
        onChangeStore = onChangeStore,
        onOpenSettings = { openAppSettings(context) }
    )
}

internal fun clockTime(epochMillis: Long, zone: ZoneId = ZoneId.systemDefault()): String =
    formatClock(epochMillis, zone)

private fun openAppSettings(context: Context) {
    val intent =
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    context.startActivity(intent)
}

/** The Check-in screen with a store chosen: stateless, so previews and tests drive every state. */
@Composable
fun CheckInContent(
    storeName: String,
    place: String?,
    status: CheckInStatus,
    result: String?,
    note: String?,
    locationDenied: Boolean,
    onCheckIn: () -> Unit,
    onChangeStore: () -> Unit,
    onOpenSettings: () -> Unit
) {
    Column(modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(Spacing.Md)) {
        Column(
            modifier = Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(Spacing.Md)
        ) {
            MqCard {
                Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Md), verticalAlignment = Alignment.Top) {
                    MqIcon(
                        R.drawable.ic_lucide_store,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Column(verticalArrangement = Arrangement.spacedBy(Spacing.Xs)) {
                        Text(
                            storeName,
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.semantics { heading() }
                        )
                        if (place != null) {
                            Text(
                                place,
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
                Box(Modifier.semantics { liveRegion = LiveRegionMode.Polite }) { StatusBadge(status) }
                result?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodyLarge,
                        modifier = Modifier.semantics {
                            liveRegion = LiveRegionMode.Polite
                        }
                    )
                }
                if (status == CheckInStatus.Waiting && result == null) {
                    Text(
                        stringResource(R.string.checkin_queued_calm),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
            if (status == CheckInStatus.Sending) {
                MqLoading(stringResource(R.string.checkin_locating))
            }
            note?.let {
                Text(
                    it,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            if (locationDenied) {
                Text(
                    stringResource(R.string.checkin_location_why),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                MqButton(
                    stringResource(R.string.checkin_open_settings),
                    onOpenSettings,
                    variant = MqButtonVariant.Quiet
                )
            }
            if (status is CheckInStatus.NeedsAttention) MqErrorPanel(status.error)
        }
        MqButton(
            stringResource(R.string.checkin_action),
            onCheckIn,
            fullWidth = true,
            loading = status == CheckInStatus.Sending,
            leadingIcon = R.drawable.ic_lucide_map_pin
        )
        MqButton(
            stringResource(R.string.checkin_change_store),
            onChangeStore,
            variant = MqButtonVariant.Secondary,
            fullWidth = true
        )
    }
}

@Composable
private fun StatusBadge(status: CheckInStatus) {
    when (status) {
        CheckInStatus.NotCheckedIn, CheckInStatus.Sending ->
            MqBadge(stringResource(R.string.checkin_not_checked_in), tone = MqTone.Neutral)
        CheckInStatus.Waiting ->
            MqBadge(
                stringResource(R.string.checkin_waiting),
                tone = MqTone.Warning,
                leadingIcon = R.drawable.ic_lucide_clock
            )
        is CheckInStatus.Done ->
            MqBadge(
                stringResource(R.string.checkin_checked_in_at, status.time),
                tone = MqTone.Success,
                leadingIcon = R.drawable.ic_lucide_circle_check
            )
        is CheckInStatus.NeedsAttention ->
            MqBadge(
                stringResource(R.string.checkin_needs_attention),
                tone = MqTone.Danger,
                leadingIcon = R.drawable.ic_lucide_triangle_alert
            )
    }
}

private const val PREVIEW_WIDTH = 375
private const val PREVIEW_HEIGHT = 640

@Composable
private fun CheckInPreview(dark: Boolean, status: CheckInStatus, denied: Boolean = false) =
    MosaiqTheme(darkTheme = dark) {
        Surface {
            Column(Modifier.fillMaxSize().padding(Spacing.Lg)) {
                CheckInContent("Centro", "Monterrey, NL", status, null, null, denied, {}, {}, {})
            }
        }
    }

@Preview(name = "Check-in idle, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun IdleLight() = CheckInPreview(false, CheckInStatus.NotCheckedIn)

@Preview(name = "Check-in waiting, dark", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun WaitingDark() = CheckInPreview(true, CheckInStatus.Waiting, denied = true)

@Preview(name = "Check-in done, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun DoneLight() = CheckInPreview(false, CheckInStatus.Done("14:32"))
