package online.maxthecoder.mosaiq.floor.location

import android.annotation.SuppressLint
import android.content.Context
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.google.android.gms.tasks.CancellationTokenSource
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import online.maxthecoder.mosaiq.floor.outbox.Fix

/** Where the phone is, as the GPS reports it, or `null` when it cannot say. */
fun interface LocationSource {
    suspend fun currentFix(): Fix?
}

/**
 * The fused provider's current fix. It sends what the phone measured, accuracy
 * included, and never decides anything: check-ins-service computes the
 * distance and the verdict (RN-56). Callers ask for the permission first.
 */
class FusedLocationSource(
    context: Context
) : LocationSource {
    private val client = LocationServices.getFusedLocationProviderClient(context)

    @SuppressLint("MissingPermission")
    override suspend fun currentFix(): Fix? = withTimeoutOrNull(TIMEOUT_MILLIS) {
        val cancellation = CancellationTokenSource()
        suspendCancellableCoroutine { continuation ->
            continuation.invokeOnCancellation { cancellation.cancel() }
            client
                .getCurrentLocation(Priority.PRIORITY_HIGH_ACCURACY, cancellation.token)
                .addOnSuccessListener { location ->
                    continuation.resume(
                        location?.let { Fix(it.latitude, it.longitude, it.accuracy.toDouble()) }
                    )
                }.addOnFailureListener { continuation.resume(null) }
        }
    }

    private companion object {
        const val TIMEOUT_MILLIS = 15_000L
    }
}
