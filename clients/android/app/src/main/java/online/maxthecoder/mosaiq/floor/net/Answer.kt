package online.maxthecoder.mosaiq.floor.net

import java.io.IOException
import retrofit2.Response

/** What a call answered: its body, or a refusal (an unreachable service is a refusal too). */
sealed interface Answer<out T> {
    data class Body<T>(val value: T) : Answer<T>

    data class Refused(val error: ApiError) : Answer<Nothing>
}

/** Runs one request; a transport failure or a non-2xx answer is a [Answer.Refused]. */
suspend fun <T> call(request: suspend () -> Response<T>): Answer<T> {
    val response =
        try {
            request()
        } catch (unreachable: IOException) {
            return Answer.Refused(ApiError.unreachable(null))
        }
    val body = response.body()
    return if (response.isSuccessful && body != null) Answer.Body(body) else Answer.Refused(ApiError.from(response))
}

/** Pages needed for `total` items at `size` per page; never less than one. */
fun totalPages(total: Int, size: Int): Int = if (size <= 0) 1 else maxOf(1, (total + size - 1) / size)
