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
import online.maxthecoder.mosaiq.floor.scan.CodeKind
import online.maxthecoder.mosaiq.floor.scan.Decoded
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqIcon
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

private const val VIEWPORT_RATIO = 4f / 3f
private const val FRAME_SHARE = 0.6f

/**
 * The product barcodes the scanner reads besides product QR codes (F27-04): the
 * products contract's `Barcode`, GTIN-8 as EAN-8, GTIN-12 as UPC-A, GTIN-13 as
 * EAN-13 and GTIN-14 as ITF-14. UPC-E is left out: its eight digits are a
 * compressed GTIN-12, not a GTIN-8.
 */
internal val PRODUCT_BARCODE_FORMATS =
    intArrayOf(Barcode.FORMAT_EAN_8, Barcode.FORMAT_UPC_A, Barcode.FORMAT_EAN_13, Barcode.FORMAT_ITF)

/** A QR code holds a product id; every other format the scanner reads is a product barcode. */
internal fun codeKind(format: Int): CodeKind =
    if (format == Barcode.FORMAT_QR_CODE) CodeKind.QR else CodeKind.PRODUCT_BARCODE

/** CameraX can deliver another frame before Compose removes the preview; only the first value wins. */
internal class DecodeOnce {
    private var accepted = false

    fun <T> offer(value: T?, onDecoded: (T) -> Unit) {
        if (value == null || accepted) return
        accepted = true
        onDecoded(value)
    }
}

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
fun CameraViewport(
    content: @Composable () -> Unit,
    description: String = stringResource(R.string.scan_camera_description),
    hint: String = stringResource(R.string.scan_hint)
) {
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
        ViewportBox(Modifier.semantics { contentDescription = description }) {
            content()
            FramingOverlay()
        }
        Text(
            hint,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth()
        )
    }
}

/** The viewport before the camera is on: the same size, with the one action that turns it on. */
@Composable
fun ViewportStart(onScan: () -> Unit, help: String = stringResource(R.string.scan_start_help)) {
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
                help,
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
 * CameraX's preview with ML Kit's analyzer for product QR codes and barcodes.
 * Every decoded value goes, with its kind, to the current `onDecoded`, not to
 * the one the effect started with. A camera that cannot be bound, or cannot
 * initialize, reports through `onError`. Leaving the composition unbinds the
 * camera, which is how the preview stops after a read.
 */
@Composable
fun CameraPreview(onDecoded: (Decoded) -> Unit, onError: (Throwable) -> Unit) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val currentDecoded by rememberUpdatedState(onDecoded)
    val currentError by rememberUpdatedState(onError)
    val controller = remember { LifecycleCameraController(context) }
    val scanner =
        remember {
            BarcodeScanning.getClient(
                BarcodeScannerOptions.Builder()
                    .setBarcodeFormats(Barcode.FORMAT_QR_CODE, *PRODUCT_BARCODE_FORMATS)
                    .build()
            )
        }
    DisposableEffect(owner) {
        var disposed = false
        val decodeOnce = DecodeOnce()
        val executor = ContextCompat.getMainExecutor(context)
        controller.setImageAnalysisAnalyzer(
            executor,
            MlKitAnalyzer(listOf(scanner), ImageAnalysis.COORDINATE_SYSTEM_VIEW_REFERENCED, executor) { result ->
                val code = result.getValue(scanner)?.firstOrNull()
                val decoded = code?.rawValue?.let { raw -> Decoded(raw, codeKind(code.format)) }
                decodeOnce.offer(decoded, currentDecoded)
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
