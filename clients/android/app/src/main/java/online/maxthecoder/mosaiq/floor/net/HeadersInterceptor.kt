package online.maxthecoder.mosaiq.floor.net

import java.util.UUID
import okhttp3.Interceptor
import okhttp3.Response
import online.maxthecoder.mosaiq.floor.auth.TokenStore

/**
 * Every request asks for JSON only (ADR-0041) and carries a fresh correlation
 * id. A signed-in request also carries the access token; the sign-in and
 * refresh calls carry none.
 */
class HeadersInterceptor(
    private val tokens: TokenStore
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.request()
        val builder =
            original
                .newBuilder()
                .header("Accept", "application/json")
                .header(ApiError.CORRELATION_HEADER, UUID.randomUUID().toString())
        val path = original.url.encodedPath
        val unauthenticated = path.endsWith("/sessions") || path.endsWith("/sessions/refresh")
        if (original.header("Authorization") == null && !unauthenticated) {
            tokens.read()?.let { builder.header("Authorization", "Bearer ${it.accessToken}") }
        }
        return chain.proceed(builder.build())
    }
}
