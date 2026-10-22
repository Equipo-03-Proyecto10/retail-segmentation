package online.maxthecoder.mosaiq.floor.net

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST

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
