package online.maxthecoder.mosaiq.floor.scan

import java.time.Instant
import java.util.UUID

/** What a decoded payload is: a product id, a product barcode, or not a product code at all. */
sealed interface Payload {
    data class ProductId(val value: Int) : Payload

    /** A GTIN-8, -12, -13 or -14, digits only: the products contract's `Barcode` (F27-04). */
    data class Barcode(val value: String) : Payload

    data object NotAProduct : Payload
}

/**
 * What kind of code the camera read. A product QR code holds a product id; a
 * product barcode holds a GTIN. An eight-digit value is either, so the kind
 * comes from the symbology the camera decoded, never from the digits.
 */
enum class CodeKind { QR, PRODUCT_BARCODE }

/** A value the camera, or the person typing it, handed over, with its kind. */
data class Decoded(val raw: String, val kind: CodeKind = CodeKind.QR)

/** The contract's `Barcode` pattern: `^([0-9]{8}|[0-9]{12,14})$`. */
private val GTIN = Regex("[0-9]{8}|[0-9]{12,14}")

/** A product QR code holds the product id and nothing else (F17-02). */
fun parseProductId(raw: String): Payload {
    val text = raw.trim()
    if (text.isEmpty() || text.any { !it.isDigit() }) return Payload.NotAProduct
    val value = text.toIntOrNull() ?: return Payload.NotAProduct
    return if (value >= 1) Payload.ProductId(value) else Payload.NotAProduct
}

/**
 * A product barcode within the contract's pattern. The check digit is not
 * checked here: the contract makes a wrong one match no product, which is the
 * answer the person sees anyway.
 */
fun parseBarcode(raw: String): Payload {
    val text = raw.trim()
    return if (GTIN.matches(text)) Payload.Barcode(text) else Payload.NotAProduct
}

/** A decoded value read as its kind: a QR code as a product id, a barcode as a GTIN. */
fun parse(decoded: Decoded): Payload = when (decoded.kind) {
    CodeKind.QR -> parseProductId(decoded.raw)
    CodeKind.PRODUCT_BARCODE -> parseBarcode(decoded.raw)
}

/**
 * One decoded code. Its `clientEventId` is generated here, once, when the code
 * is decoded (ADR-0041): the queued event keeps it, so a retry is the same event.
 */
data class Scan(
    val payload: Payload,
    val clientEventId: String,
    val occurredAt: Instant
)

/**
 * Turns the analyzer's stream of frames into scans. The analyzer reports the
 * same code on every frame it stays in view; that is one scan until a different
 * code is decoded or the user asks to scan again.
 */
class ScanSession(
    private val newId: () -> String = { UUID.randomUUID().toString() }
) {
    private var last: Decoded? = null

    /** A scan for a new code, or null for a frame that repeats the last one. */
    @Synchronized
    fun onDecoded(raw: String, now: Instant = Instant.now()): Scan? = onDecoded(Decoded(raw), now)

    /** The same, for a code whose kind the camera reported. */
    @Synchronized
    fun onDecoded(decoded: Decoded, now: Instant = Instant.now()): Scan? {
        if (decoded == last) return null
        last = decoded
        return Scan(parse(decoded), newId(), now)
    }

    /** "Scan again": the same code may count once more. */
    @Synchronized
    fun scanAgain() {
        last = null
    }
}
