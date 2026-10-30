package online.maxthecoder.mosaiq.floor.ui

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqCard
import online.maxthecoder.mosaiq.floor.ui.components.MqEmptyState
import online.maxthecoder.mosaiq.floor.ui.components.MqIcon
import online.maxthecoder.mosaiq.floor.ui.components.MqTextField
import online.maxthecoder.mosaiq.floor.ui.scan.CameraPreview
import online.maxthecoder.mosaiq.floor.ui.scan.CameraViewport
import online.maxthecoder.mosaiq.floor.ui.scan.ScanAnswer
import online.maxthecoder.mosaiq.floor.ui.scan.ViewportStart
import online.maxthecoder.mosaiq.floor.ui.scan.openAppSettings
import online.maxthecoder.mosaiq.floor.ui.scan.reconcilePermission
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** Where the camera stands for this screen. */
enum class CameraPermission { NOT_ASKED, GRANTED, DENIED }

private fun cameraGranted(context: Context): Boolean =
    ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED

/**
 * The scan screen (F17-02). The camera permission is asked only when the user
 * first taps Scan, and it is checked again every time the app resumes, so a
 * permission revoked in Settings shows the denied state. Denied, including
 * "don't ask again", the screen explains why and offers typing the product
 * number instead. A read stops the camera; "Scan another" starts it again.
 */
@Composable
fun ScanScreen(
    model: ScanViewModel,
    store: ChosenStore?,
    onPickStore: () -> Unit,
    onAddProduct: ((Product) -> Unit)? = null
) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    var permission by rememberSaveable {
        mutableStateOf(if (cameraGranted(context)) CameraPermission.GRANTED else CameraPermission.NOT_ASKED)
    }
    var scanning by rememberSaveable { mutableStateOf(false) }
    var cameraError by remember { mutableStateOf(false) }
    val result by model.result.collectAsState()
    val launcher =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            permission = if (granted) CameraPermission.GRANTED else CameraPermission.DENIED
            cameraError = false
            scanning = granted
        }

    val currentPermission by rememberUpdatedState(permission)
    DisposableEffect(owner, context) {
        val observer =
            LifecycleEventObserver { _, event ->
                if (event == Lifecycle.Event.ON_RESUME) {
                    val before = currentPermission
                    val after = reconcilePermission(before, cameraGranted(context))
                    if (after != before) {
                        permission = after
                        // Allowed again in Settings: pick up where the user left off.
                        scanning = after == CameraPermission.GRANTED && before == CameraPermission.DENIED
                    }
                }
            }
        owner.lifecycle.addObserver(observer)
        onDispose { owner.lifecycle.removeObserver(observer) }
    }

    // Any answer ends the scan: the camera is off until "Scan another".
    LaunchedEffect(result) {
        if (result !is ScanResult.Idle) scanning = false
    }

    ScanContent(
        model = model,
        store = store,
        permission = permission,
        scanning = scanning,
        onPickStore = onPickStore,
        onScan = {
            cameraError = false
            if (permission == CameraPermission.GRANTED) {
                scanning = true
            } else {
                launcher.launch(Manifest.permission.CAMERA)
            }
        },
        onAddProduct = onAddProduct,
        onOpenSettings = { openAppSettings(context) },
        onGrantAccess = { launcher.launch(Manifest.permission.CAMERA) },
        cameraError = cameraError,
        onRetryCamera = { cameraError = false },
        camera = { onDecoded -> CameraPreview(onDecoded = onDecoded, onError = { cameraError = true }) }
    )
}

/**
 * The screen without the camera and the permission prompt, so it can be tested.
 * The camera shows only while [scanning] and nothing has been read: an answer
 * takes its place, and "Scan another" clears the answer and calls [onScan].
 */
@Composable
fun ScanContent(
    model: ScanViewModel,
    store: ChosenStore?,
    permission: CameraPermission,
    scanning: Boolean,
    onPickStore: () -> Unit,
    onScan: () -> Unit,
    onAddProduct: ((Product) -> Unit)? = null,
    onOpenSettings: () -> Unit = {},
    onGrantAccess: () -> Unit = {},
    cameraError: Boolean = false,
    onRetryCamera: () -> Unit = {},
    camera: @Composable (onDecoded: (String) -> Unit) -> Unit
) {
    var lastCode by rememberSaveable { mutableStateOf("") }
    val result by model.result.collectAsState()
    ScanBody(
        store = store,
        permission = permission,
        scanning = scanning,
        result = result,
        onPickStore = onPickStore,
        onScan = onScan,
        onScanAnother = {
            model.scanAgain()
            onScan()
        },
        onRetryLookup = { if (lastCode.isEmpty()) model.scanAgain() else model.onManualEntry(lastCode) },
        onLookUp = {
            lastCode = it
            model.onManualEntry(it)
        },
        onAddProduct = onAddProduct,
        onOpenSettings = onOpenSettings,
        onGrantAccess = onGrantAccess,
        cameraError = cameraError,
        onRetryCamera = onRetryCamera,
        camera = {
            camera { raw ->
                lastCode = raw
                model.onDecoded(raw)
            }
        }
    )
}

/** Every state of the scan screen from plain values, for previews and tests. */
@Composable
fun ScanBody(
    store: ChosenStore?,
    permission: CameraPermission,
    scanning: Boolean,
    result: ScanResult,
    onPickStore: () -> Unit,
    onScan: () -> Unit,
    onScanAnother: () -> Unit,
    onRetryLookup: () -> Unit,
    onLookUp: (String) -> Unit,
    onAddProduct: ((Product) -> Unit)?,
    onOpenSettings: () -> Unit,
    onGrantAccess: () -> Unit,
    cameraError: Boolean,
    onRetryCamera: () -> Unit,
    camera: @Composable () -> Unit
) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(Spacing.Lg)
    ) {
        if (store == null) {
            MqEmptyState(
                icon = R.drawable.ic_lucide_store,
                title = stringResource(R.string.scan_no_store_title),
                message = stringResource(R.string.scan_no_store_message),
                actionLabel = stringResource(R.string.scan_pick_store),
                onAction = onPickStore
            )
            return@Column
        }
        Text(
            stringResource(R.string.scan_store_caption, store.name),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        when {
            permission == CameraPermission.DENIED -> CameraDenied(onOpenSettings, onGrantAccess)
            result !is ScanResult.Idle -> Unit
            cameraError && scanning -> CameraFailed(onRetryCamera)
            scanning -> CameraViewport(camera)
            else -> ViewportStart(onScan)
        }
        ScanAnswer(result, onScanAnother, onRetryLookup, onPickStore, onAddProduct)
        ManualEntry(onLookUp)
    }
}

@Composable
private fun CameraDenied(onOpenSettings: () -> Unit, onGrantAccess: () -> Unit) {
    MqCard(Modifier.testTag("camera-denied")) {
        MqIcon(
            R.drawable.ic_lucide_triangle_alert,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Text(
            stringResource(R.string.scan_denied_title),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        Text(
            stringResource(R.string.scan_denied_message),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        MqButton(stringResource(R.string.scan_grant_access), onGrantAccess, fullWidth = true)
        MqButton(
            stringResource(R.string.scan_open_settings),
            onOpenSettings,
            variant = MqButtonVariant.Secondary,
            fullWidth = true
        )
    }
}

@Composable
private fun CameraFailed(onRetry: () -> Unit) {
    MqCard(Modifier.testTag("camera-error")) {
        MqIcon(R.drawable.ic_lucide_circle_alert, contentDescription = null, tint = MaterialTheme.colorScheme.error)
        Text(
            stringResource(R.string.scan_camera_error_title),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        Text(
            stringResource(R.string.scan_camera_error_message),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        MqButton(
            stringResource(R.string.scan_retry),
            onRetry,
            variant = MqButtonVariant.Secondary,
            fullWidth = true,
            leadingIcon = R.drawable.ic_lucide_refresh_cw
        )
    }
}

@Composable
private fun ManualEntry(onLookUp: (String) -> Unit) {
    var typed by rememberSaveable { mutableStateOf("") }
    val submit = { if (typed.isNotBlank()) onLookUp(typed.trim()) }
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
        Text(
            stringResource(R.string.scan_manual_title),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        MqTextField(
            value = typed,
            onValueChange = { typed = it },
            label = stringResource(R.string.scan_manual_label),
            supportingText = stringResource(R.string.scan_manual_help),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number, imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { submit() }),
            modifier = Modifier.testTag("manual-product-id")
        )
        MqButton(
            stringResource(R.string.scan_look_up),
            { submit() },
            variant = MqButtonVariant.Secondary,
            enabled = typed.isNotBlank(),
            fullWidth = true
        )
    }
}
