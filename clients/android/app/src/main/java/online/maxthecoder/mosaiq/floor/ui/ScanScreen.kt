package online.maxthecoder.mosaiq.floor.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.ImageAnalysis
import androidx.camera.mlkit.vision.MlKitAnalyzer
import androidx.camera.view.LifecycleCameraController
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import online.maxthecoder.mosaiq.floor.session.ChosenStore

/** Where the camera stands for this screen. */
enum class CameraPermission { NOT_ASKED, GRANTED, DENIED }

/**
 * The scan screen (F17-02). The camera permission is asked only when the user
 * first taps Scan; denied, including "don't ask again", the screen explains
 * why and offers typing the product id instead.
 */
@Composable
fun ScanScreen(
    model: ScanViewModel,
    store: ChosenStore?,
    onPickStore: () -> Unit,
    onAddProduct: ((online.maxthecoder.mosaiq.floor.net.Product) -> Unit)? = null
) {
    val context = LocalContext.current
    var permission by rememberSaveable {
        mutableStateOf(
            if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED
            ) {
                CameraPermission.GRANTED
            } else {
                CameraPermission.NOT_ASKED
            }
        )
    }
    var scanning by rememberSaveable { mutableStateOf(false) }
    val launcher =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            permission = if (granted) CameraPermission.GRANTED else CameraPermission.DENIED
            scanning = granted
        }
    ScanContent(
        model = model,
        store = store,
        permission = permission,
        scanning = scanning,
        onPickStore = onPickStore,
        onScan = {
            if (permission == CameraPermission.GRANTED) {
                scanning = true
            } else {
                launcher.launch(Manifest.permission.CAMERA)
            }
        },
        onAddProduct = onAddProduct,
        camera = { CameraPreview(onDecoded = model::onDecoded) }
    )
}

/** The screen without the camera and the permission prompt, so it can be tested. */
@Composable
fun ScanContent(
    model: ScanViewModel,
    store: ChosenStore?,
    permission: CameraPermission,
    scanning: Boolean,
    onPickStore: () -> Unit,
    onScan: () -> Unit,
    onAddProduct: ((online.maxthecoder.mosaiq.floor.net.Product) -> Unit)? = null,
    camera: @Composable () -> Unit
) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        if (store == null) {
            Text("Pick your store first: a scan shows the stock of the store you are checked in at.")
            Button(onClick = onPickStore) { Text("Pick a store") }
            return@Column
        }
        Text("Store: ${store.name}", style = MaterialTheme.typography.titleMedium)
        when {
            permission == CameraPermission.DENIED -> {
                Text(
                    "The camera is off for this app. It is needed only to read a product's QR code; " +
                        "you can type the product id instead, or allow the camera in the system settings.",
                    modifier = Modifier.testTag("camera-denied")
                )
            }
            scanning -> camera()
            else -> Button(onClick = onScan) { Text("Scan") }
        }
        ManualEntry(model)
        ScanAnswer(model, onAddProduct)
    }
}

@Composable
private fun ManualEntry(model: ScanViewModel) {
    var typed by rememberSaveable { mutableStateOf("") }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(
            value = typed,
            onValueChange = { typed = it },
            label = { Text("Product id") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.weight(1f).testTag("manual-product-id")
        )
        OutlinedButton(onClick = { model.onManualEntry(typed) }) { Text("Look up") }
    }
}

@Composable
private fun ScanAnswer(model: ScanViewModel, onAddProduct: ((online.maxthecoder.mosaiq.floor.net.Product) -> Unit)?) {
    val result by model.result.collectAsState()
    when (val current = result) {
        ScanResult.Idle -> Unit
        ScanResult.Looking -> Text("Looking up the product…")
        ScanResult.NoStore -> Text("Pick your store first.")
        ScanResult.NotAProduct -> Text("Not a product code.")
        ScanResult.ProductNotFound -> Text("Product not found.")
        is ScanResult.Failed -> ErrorPanel(current.error)
        is ScanResult.Shown -> {
            val product = current.product
            Text(product.name, style = MaterialTheme.typography.titleLarge)
            Text("SKU ${product.sku} · ${product.listPrice}")
            if (!product.isActive) Text("Retired: this product is no longer sold.")
            when {
                current.stockError != null -> ErrorPanel(current.stockError)
                current.quantity == null -> Text("Not stocked at this store.")
                else -> Text("${current.quantity} in stock at ${current.storeName}.")
            }
            onAddProduct?.let { add ->
                Button(onClick = { add(product) }) { Text("Add to sale") }
            }
        }
    }
    if (result !is ScanResult.Idle && result !is ScanResult.NoStore) {
        OutlinedButton(onClick = model::scanAgain) { Text("Scan again") }
    }
}

/** CameraX's preview with ML Kit's QR analyzer; every decoded value goes to `onDecoded`. */
@Composable
private fun CameraPreview(onDecoded: (String) -> Unit) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val controller = remember { LifecycleCameraController(context) }
    val scanner =
        remember {
            BarcodeScanning.getClient(
                BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
            )
        }
    DisposableEffect(owner) {
        val executor = ContextCompat.getMainExecutor(context)
        controller.setImageAnalysisAnalyzer(
            executor,
            MlKitAnalyzer(listOf(scanner), ImageAnalysis.COORDINATE_SYSTEM_VIEW_REFERENCED, executor) { result ->
                result.getValue(scanner)?.firstOrNull()?.rawValue?.let(onDecoded)
            }
        )
        controller.bindToLifecycle(owner)
        onDispose {
            controller.unbind()
            scanner.close()
        }
    }
    AndroidView(
        factory = { PreviewView(it).also { view -> view.controller = controller } },
        modifier = Modifier.fillMaxWidth().height(PREVIEW_HEIGHT.dp)
    )
}

private const val PREVIEW_HEIGHT = 280
