package online.maxthecoder.mosaiq.floor.net

import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Response
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
    events: SessionEvents,
    timeoutSeconds: Long = TIMEOUT_SECONDS
) {
    private val base = baseUrl.toHttpUrl()

    /** Sign-in and refresh go out without an authenticator, so they cannot loop. */
    private val plainClient =
        OkHttpClient
            .Builder()
            .addInterceptor(HeadersInterceptor(tokens))
            .callTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .build()

    val authenticator = TokenAuthenticator(base, tokens, events, plainClient)

    val client: OkHttpClient =
        plainClient
            .newBuilder()
            .authenticator(authenticator)
            .build()

    // Registration sends its null email or phone explicitly, as the contract allows.
    private val registrationJson = Json(json) { explicitNulls = true }

    private val retrofit =
        Retrofit
            .Builder()
            .baseUrl(base)
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()

    val auth: AuthApi = retrofit.create(AuthApi::class.java)
    val users: UsersApi = retrofit.create(UsersApi::class.java)
    val stores: StoresApi = retrofit.create(StoresApi::class.java)
    val products: ProductsApi = retrofit.create(ProductsApi::class.java)
    val stock: StockApi = retrofit.create(StockApi::class.java)
    val customers: CustomersApi = retrofit.create(CustomersApi::class.java)

    // Registration has no idempotency contract. Even auth, redirects and
    // transport recovery must not automatically replay this POST.
    val registration: CustomersApi =
        Retrofit.Builder()
            .baseUrl(base)
            .client(
                plainClient.newBuilder()
                    .retryOnConnectionFailure(false)
                    .followRedirects(false)
                    .followSslRedirects(false)
                    .addNetworkInterceptor(NoImmediateReplay)
                    .build()
            )
            .addConverterFactory(
                registrationJson.asConverterFactory("application/json".toMediaType())
            )
            .build().create(CustomersApi::class.java)
    val preferences: CustomerPreferencesApi = retrofit.create(CustomerPreferencesApi::class.java)
    val categories: CategoriesApi = retrofit.create(CategoriesApi::class.java)
    val sales: SalesApi = retrofit.create(SalesApi::class.java)
    val segments: SegmentsApi = retrofit.create(SegmentsApi::class.java)
    val recommendations: RecommendationsApi = retrofit.create(RecommendationsApi::class.java)
    val engagement: EngagementApi = retrofit.create(EngagementApi::class.java)

    /** The gateway, for the floor-write queue's raw requests. */
    val gateway: okhttp3.HttpUrl get() = base

    /**
     * OkHttp itself replays a request once after a 503 that says `Retry-After: 0`.
     * Registration must never be sent twice by the app, so that header is removed
     * before OkHttp can act on it.
     */
    private object NoImmediateReplay : Interceptor {
        override fun intercept(chain: Interceptor.Chain): Response {
            val response = chain.proceed(chain.request())
            return if (response.code == 503 && response.header("Retry-After")?.trim() == "0") {
                response.newBuilder().removeHeader("Retry-After").build()
            } else {
                response
            }
        }
    }

    private companion object {
        const val TIMEOUT_SECONDS = 20L
    }
}
