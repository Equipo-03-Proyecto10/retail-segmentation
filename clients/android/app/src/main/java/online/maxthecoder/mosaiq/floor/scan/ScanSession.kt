package online.maxthecoder.mosaiq.floor.scan

import java.time.Instant
import java.util.UUID

/** What a decoded payload is: a product id, or not a product code at all. */
sealed interface Payload {
    data class ProductId(val value: Int) : Payload

    data object NotAProduct : Payload
}

/** A product QR code holds the product id and nothing else (F17-02). */
fun parseProductId(raw: String): Payload {
    val text = raw.trim()
    if (text.isEmpty() || text.any { !it.isDigit() }) return Payload.NotAProduct
    val value = text.toIntOrNull() ?: return Payload.NotAProduct
    return if (value >= 1) Payload.ProductId(value) else Payload.NotAProduct
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
    private var last: String? = null

    /** A scan for a new code, or null for a frame that repeats the last one. */
    @Synchronized
    fun onDecoded(raw: String, now: Instant = Instant.now()): Scan? {
        if (raw == last) return null
        last = raw
        return Scan(parseProductId(raw), newId(), now)
    }

    /** "Scan again": the same code may count once more. */
    @Synchronized
    fun scanAgain() {
        last = null
    }
}
