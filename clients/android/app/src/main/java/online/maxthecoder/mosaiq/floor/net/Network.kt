package online.maxthecoder.mosaiq.floor.net

import java.util.concurrent.TimeUnit
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.TokenStore
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/**
 * The clients every screen uses. There is deliberately no logging
 * interceptor: a request or response body may hold a token, and no token is
 * ever written to Logcat.
 */
class Network(
    baseUrl: String,
    tokens: TokenStore,
    events: SessionEvents
) {
    private val base = baseUrl.toHttpUrl()

    /** Sign-in and refresh go out without an authenticator, so they cannot loop. */
    private val plainClient =
        OkHttpClient
            .Builder()
            .addInterceptor(HeadersInterceptor(tokens))
            .callTimeout(TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .build()

    val authenticator = TokenAuthenticator(base, tokens, events, plainClient)

    val client: OkHttpClient =
        plainClient
            .newBuilder()
            .authenticator(authenticator)
            .build()

    private val retrofit =
        Retrofit
            .Builder()
            .baseUrl(base)
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()

    val auth: AuthApi = retrofit.create(AuthApi::class.java)
    val users: UsersApi = retrofit.create(UsersApi::class.java)

    private companion object {
        const val TIMEOUT_SECONDS = 20L
    }
}
