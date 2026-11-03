package online.maxthecoder.mosaiq.floor.ui.scan

import androidx.camera.core.ImageAnalysis
import androidx.camera.mlkit.vision.MlKitAnalyzer
import androidx.camera.view.LifecycleCameraController
import androidx.camera.view.PreviewView
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqIcon
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

private const val VIEWPORT_RATIO = 4f / 3f
private const val FRAME_SHARE = 0.6f

/** The viewport's box: full width, clipped to the theme shape, with a neutral ground behind it. */
@Composable
private fun ViewportBox(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Box(
        modifier =
        modifier
            .fillMaxWidth()
            .aspectRatio(VIEWPORT_RATIO)
            .clip(MaterialTheme.shapes.small)
            .background(MaterialTheme.colorScheme.surfaceContainerHigh),
        contentAlignment = Alignment.Center
    ) { content() }
}

/** The live camera with its framing overlay, and the hint under it. */
@Composable
fun CameraViewport(content: @Composable () -> Unit) {
    val description = stringResource(R.string.scan_camera_description)
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
        ViewportBox(Modifier.semantics { contentDescription = description }) {
            content()
            FramingOverlay()
        }
        Text(
            stringResource(R.string.scan_hint),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth()
        )
    }
}

/** The viewport before the camera is on: the same size, with the one action that turns it on. */
@Composable
fun ViewportStart(onScan: () -> Unit) {
    ViewportBox {
        Column(
            modifier = Modifier.padding(Spacing.Lg),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(Spacing.Md)
        ) {
            MqIcon(
                R.drawable.ic_lucide_scan_line,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                size = Spacing.X3l
            )
            Text(
                stringResource(R.string.scan_start_help),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center
            )
            MqButton(stringResource(R.string.scan_start), onScan)
        }
    }
}

/** A rounded square in the middle of the viewport where the code should sit. */
@Composable
private fun FramingOverlay() {
    val color = MaterialTheme.colorScheme.primary
    Canvas(Modifier.fillMaxSize()) {
        val side = size.minDimension * FRAME_SHARE
        drawRoundRect(
            color = color,
            topLeft = Offset((size.width - side) / 2, (size.height - side) / 2),
            size = Size(side, side),
            cornerRadius = CornerRadius(Spacing.Md.toPx()),
            style = Stroke(width = Spacing.Xs.toPx())
        )
    }
}

/**
 * CameraX's preview with ML Kit's QR analyzer. Every decoded value goes to the
 * current `onDecoded`, not to the one the effect started with. A camera that
 * cannot be bound, or cannot initialize, reports through `onError`. Leaving the
 * composition unbinds the camera, which is how the preview stops after a read.
 */
@Composable
fun CameraPreview(onDecoded: (String) -> Unit, onError: (Throwable) -> Unit) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val currentDecoded by rememberUpdatedState(onDecoded)
    val currentError by rememberUpdatedState(onError)
    val controller = remember { LifecycleCameraController(context) }
    val scanner =
        remember {
            BarcodeScanning.getClient(
                BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
            )
        }
    DisposableEffect(owner) {
        var disposed = false
        val executor = ContextCompat.getMainExecutor(context)
        controller.setImageAnalysisAnalyzer(
            executor,
            MlKitAnalyzer(listOf(scanner), ImageAnalysis.COORDINATE_SYSTEM_VIEW_REFERENCED, executor) { result ->
                result.getValue(scanner)?.firstOrNull()?.rawValue?.let { currentDecoded(it) }
            }
        )
        try {
            controller.bindToLifecycle(owner)
            val initialization = controller.initializationFuture
            initialization.addListener(
                {
                    try {
                        initialization.get()
                    } catch (failure: Exception) {
                        if (!disposed) currentError(failure)
                    }
                },
                executor
            )
        } catch (failure: RuntimeException) {
            currentError(failure)
        }
        onDispose {
            disposed = true
            controller.clearImageAnalysisAnalyzer()
            controller.unbind()
            scanner.close()
        }
    }
    AndroidView(
        factory = { PreviewView(it).also { view -> view.controller = controller } },
        modifier = Modifier.fillMaxSize()
    )
}
