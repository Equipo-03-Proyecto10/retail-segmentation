package online.maxthecoder.mosaiq.floor.alerts

import java.nio.ByteBuffer
import java.security.MessageDigest
import java.time.Instant
import java.util.UUID
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import online.maxthecoder.mosaiq.floor.net.CaptureIncidentRequest
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState

/**
 * The report of a refused queued write (ADR-0071 section 3). Everything in it comes from the refused row as
 * it was stored, so every resend, and every device holding the same row, reports the same incident.
 */
object CaptureIncidents {
    /**
     * The one fixed namespace of `client_incident_id`: the UUIDv5, in the URL namespace, of
     * `https://mosaiq.maxthecoder.online/ns/capture-incident`.
     */
    val NAMESPACE: UUID = UUID.fromString("69756a29-5973-5fac-8e13-349300b2077d")

    private val CORRELATION = Regex("^[0-9A-Za-z-]{1,64}$")
    private val CODE = Regex("^[a-z][a-z0-9_]{0,63}$")

    /** `source_type` of each queued operation; a report itself is never reported. */
    fun sourceType(operation: Operation): String? = when (operation) {
        Operation.SALE -> "SALE"
        Operation.CHECK_IN -> "CHECK_IN"
        Operation.QR_SCAN,
        Operation.RECO_CLICK,
        Operation.CAMPAIGN_EXPOSURE,
        Operation.CART_ADD,
        Operation.CART_REMOVE -> "ENGAGEMENT_EVENT"
        Operation.CAPTURE_INCIDENT -> null
    }

    /** The idempotency key: the UUIDv5 of [sourceOperationId] under [NAMESPACE] (RFC 9562), never random. */
    fun clientIncidentId(sourceOperationId: String): String {
        val digest = MessageDigest.getInstance("SHA-1")
        digest.update(
            ByteBuffer.allocate(
                16
            ).putLong(NAMESPACE.mostSignificantBits).putLong(NAMESPACE.leastSignificantBits).array()
        )
        digest.update(sourceOperationId.lowercase().toByteArray(Charsets.UTF_8))
        val hash = digest.digest()
        hash[6] = ((hash[6].toInt() and 0x0f) or 0x50).toByte()
        hash[8] = ((hash[8].toInt() and 0x3f) or 0x80).toByte()
        val buffer = ByteBuffer.wrap(hash, 0, 16)
        return UUID(buffer.long, buffer.long).toString()
    }

    /**
     * The report of [row], or `null` when the contract cannot carry it: not a final 4xx refusal (an
     * `invalid_path` refusal never reached a service), no correlation id or code of the contract's shape, a
     * client id that is no UUID, or a body without a store.
     */
    fun reportOf(row: OutboxRow): CaptureIncidentRequest? {
        val type = sourceType(row.operation) ?: return null
        val status = row.status ?: return null
        if (row.state != RowState.REFUSED || status !in 400..499) return null
        val correlation = row.correlationId?.takeIf { CORRELATION.matches(it) } ?: return null
        val code = row.code?.takeIf { CODE.matches(it) } ?: return null
        val operationId = runCatching { UUID.fromString(row.clientId).toString() }.getOrNull() ?: return null
        val body = runCatching { json.parseToJsonElement(row.body) as? JsonObject }.getOrNull() ?: return null
        val storeId = body["store_id"]?.jsonPrimitive?.intOrNull ?: return null
        val lines =
            if (row.operation == Operation.SALE) {
                runCatching { body["lines"]?.jsonArray?.size }.getOrNull()?.takeIf { it > 0 }
            } else {
                null
            }
        return CaptureIncidentRequest(
            clientIncidentId = clientIncidentId(operationId),
            sourceType = type,
            sourceOperationId = operationId,
            sourceCorrelationId = correlation,
            storeId = storeId,
            refusedStatus = status,
            refusedCode = code,
            attempts = row.attempts.coerceAtLeast(1),
            occurredAt = Instant.ofEpochMilli(row.createdAt).toString(),
            lineCount = lines
        )
    }
}
