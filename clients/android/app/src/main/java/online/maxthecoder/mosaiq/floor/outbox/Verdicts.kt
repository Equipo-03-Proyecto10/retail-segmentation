package online.maxthecoder.mosaiq.floor.outbox

import kotlin.math.roundToInt
import online.maxthecoder.mosaiq.floor.net.CheckInResult

/** The server's verdict in plain words. The app only reports it (RN-56). */
fun describe(result: CheckInResult): String {
    val distance = result.distanceM?.roundToInt()
    val reason =
        when (result.reason) {
            "within_geofence" -> "You're at the store: $distance m from it, inside its geofence."
            "outside_geofence" -> "You're $distance m from the store, outside its geofence."
            "low_accuracy" ->
                "Your location was too imprecise to tell" +
                    (result.accuracyM?.let { " (within ${it.roundToInt()} m)" } ?: "") + "."
            "store_location_unknown" -> "This store has no location set yet, so the check-in could not be measured."
            "no_fix" -> "No location was sent, so the check-in could not be measured."
            else -> "The server recorded the check-in (${result.reason})."
        }
    val verdict = if (result.verified) "Verified." else "Not verified."
    return "$reason $verdict"
}

/** Extra advice for a refusal the user can fix on the phone; `null` when there is none. */
fun refusalAdvice(code: String?): String? = when (code) {
    "future_dated" -> "The sale is dated in the future. Check the phone's clock, then record the sale again."
    else -> null
}
