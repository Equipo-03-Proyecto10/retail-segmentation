package online.maxthecoder.mosaiq.floor.outbox

import kotlin.math.roundToInt
import online.maxthecoder.mosaiq.floor.AppText
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.CheckInResult

/** The server's verdict in plain words. The app only reports it (RN-56). */
fun describe(result: CheckInResult): String {
    val distance = result.distanceM?.roundToInt()
    val reason =
        when (result.reason) {
            "within_geofence" -> AppText.get(R.string.verdict_within_geofence, distance.toString())
            "outside_geofence" -> AppText.get(R.string.verdict_outside_geofence, distance.toString())
            "low_accuracy" ->
                result.accuracyM?.let { AppText.get(R.string.verdict_low_accuracy_within, it.roundToInt()) }
                    ?: AppText.get(R.string.verdict_low_accuracy)
            "store_location_unknown" -> AppText.get(R.string.verdict_store_location_unknown)
            "no_fix" -> AppText.get(R.string.verdict_no_fix)
            else -> AppText.get(R.string.verdict_recorded, result.reason)
        }
    val verdict = AppText.get(if (result.verified) R.string.verdict_verified else R.string.verdict_not_verified)
    return AppText.get(R.string.verdict_joined, reason, verdict)
}

/** Extra advice for a refusal the user can fix on the phone; `null` when there is none. */
fun refusalAdvice(code: String?): String? = when (code) {
    "future_dated" -> AppText.get(R.string.refusal_future_dated)
    else -> null
}

/**
 * The reason a write was refused, in plain words, for the refusals whose service message is about
 * the contract more than the person; `null` keeps the service's own message.
 */
fun refusalReason(code: String?): String? = when (code) {
    "forbidden_role" -> AppText.get(R.string.refusal_forbidden_role)
    "forbidden_store" -> AppText.get(R.string.refusal_forbidden_store)
    else -> null
}

/**
 * What the service answered to this row, as the error screens show it. [fallbackMessage] is what a
 * refusal with no message of its own says; the screens that list rows each have their own wording.
 */
fun OutboxRow.asError(fallbackMessage: String = AppText.get(R.string.outbox_refused_default)): ApiError = ApiError(
    status = status ?: 0,
    code = code ?: "refused",
    message = message ?: fallbackMessage,
    correlationId = correlationId
)
