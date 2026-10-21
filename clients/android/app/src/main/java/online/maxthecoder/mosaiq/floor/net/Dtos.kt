package online.maxthecoder.mosaiq.floor.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** `SignInRequest` of contracts/auth/openapi.yaml. */
@Serializable
data class SignInRequest(
    val email: String,
    val password: String,
    @SerialName("device_label") val deviceLabel: String
)

/** `RefreshRequest` of contracts/auth/openapi.yaml. */
@Serializable
data class RefreshRequest(
    @SerialName("refresh_token") val refreshToken: String
)

/** `TokenResult` of contracts/auth/openapi.yaml. */
@Serializable
data class TokenResult(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("token_type") val tokenType: String,
    @SerialName("expires_in") val expiresIn: Int,
    @SerialName("expires_at") val expiresAt: String
)

/** The fields of users' `User` this app shows; unknown fields are ignored. */
@Serializable
data class CurrentUser(
    @SerialName("user_id") val userId: String,
    val name: String,
    @SerialName("role_code") val roleCode: String
)

/** The common error envelope (contracts/common/v1). */
@Serializable
data class ErrorEnvelope(
    val code: String,
    val message: String,
    @SerialName("correlation_id") val correlationId: String? = null
)
