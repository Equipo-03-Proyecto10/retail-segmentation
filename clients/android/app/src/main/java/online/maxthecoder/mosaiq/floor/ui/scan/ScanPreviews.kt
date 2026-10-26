package online.maxthecoder.mosaiq.floor.ui.scan

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.Product
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.CameraPermission
import online.maxthecoder.mosaiq.floor.ui.ScanBody
import online.maxthecoder.mosaiq.floor.ui.ScanResult
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

private const val PREVIEW_WIDTH = 375
private const val PREVIEW_HEIGHT = 760
private const val SAMPLE_STOCK = 14

private val sample = Product(120, "SKU-120", "Cotton shirt", 4, "12.5", true)

@Composable
private fun Frame(dark: Boolean, content: @Composable () -> Unit) = MosaiqTheme(darkTheme = dark) {
    Surface(color = MaterialTheme.colorScheme.background) {
        Box(Modifier.fillMaxSize().padding(Spacing.Lg)) { content() }
    }
}

@Composable
private fun Body(
    permission: CameraPermission = CameraPermission.GRANTED,
    scanning: Boolean = false,
    result: ScanResult = ScanResult.Idle,
    store: ChosenStore? = ChosenStore(3, "Coyoacan"),
    cameraError: Boolean = false
) = ScanBody(
    store = store,
    permission = permission,
    scanning = scanning,
    result = result,
    onPickStore = {},
    onScan = {},
    onScanAnother = {},
    onRetryLookup = {},
    onLookUp = {},
    onAddProduct = {},
    onOpenSettings = {},
    onGrantAccess = {},
    cameraError = cameraError,
    onRetryCamera = {},
    camera = {}
)

@Preview(name = "Ready, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun ReadyLight() = Frame(dark = false) { Body() }

@Preview(name = "Ready, dark", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun ReadyDark() = Frame(dark = true) { Body() }

@Preview(name = "Scanning, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun ScanningLight() = Frame(dark = false) { Body(scanning = true) }

@Preview(name = "Scanning, dark", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun ScanningDark() = Frame(dark = true) { Body(scanning = true) }

@Preview(name = "Denied, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun DeniedLight() = Frame(dark = false) { Body(permission = CameraPermission.DENIED) }

@Preview(name = "Denied, dark", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun DeniedDark() = Frame(dark = true) { Body(permission = CameraPermission.DENIED) }

@Preview(name = "Camera error, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun CameraErrorLight() = Frame(dark = false) { Body(scanning = true, cameraError = true) }

@Preview(name = "Camera error, dark", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun CameraErrorDark() = Frame(dark = true) { Body(scanning = true, cameraError = true) }

@Preview(name = "Product, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun ProductLight() = Frame(dark = false) {
    Body(result = ScanResult.Shown(sample, "Coyoacan", SAMPLE_STOCK, null, 1L))
}

@Preview(name = "Product out of stock, dark", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun ProductOutDark() = Frame(dark = true) {
    Body(result = ScanResult.Shown(sample, "Coyoacan", 0, null, 1L))
}

@Preview(name = "Not found, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun NotFoundLight() = Frame(dark = false) { Body(result = ScanResult.ProductNotFound) }

@Preview(name = "Offline, dark", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun OfflineDark() = Frame(dark = true) {
    Body(result = ScanResult.Failed(ApiError.unreachable(null)))
}

@Preview(name = "No store, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun NoStoreLight() = Frame(dark = false) { Body(store = null) }
