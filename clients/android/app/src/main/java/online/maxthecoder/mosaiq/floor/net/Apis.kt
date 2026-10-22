package online.maxthecoder.mosaiq.floor.net

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query

/** auth-service's sessions (contracts/auth/openapi.yaml). */
interface AuthApi {
    @POST(Services.AUTH + "sessions")
    suspend fun createSession(@Body request: SignInRequest): Response<TokenResult>

    @POST(Services.AUTH + "sessions/refresh")
    suspend fun refreshSession(@Body request: RefreshRequest): Response<TokenResult>

    @DELETE(Services.AUTH + "sessions/current")
    suspend fun deleteCurrentSession(): Response<Unit>
}

/** users-service's caller profile (contracts/users/openapi.yaml). */
interface UsersApi {
    @GET(Services.USERS + "users/me")
    suspend fun getCurrentUser(): Response<CurrentUser>
}

/** stores-service's directory (contracts/stores/openapi.yaml). */
interface StoresApi {
    @GET(Services.STORES + "stores")
    suspend fun listStores(
        @Query("active") active: Boolean = true,
        @Query("city") city: String? = null,
        @Query("page") page: Int = 1,
        @Query("size") size: Int = 20
    ): Response<StorePage>
}
