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

/** `PageInfo` of contracts/common/v1. */
@Serializable
data class PageInfo(
    val page: Int,
    val size: Int,
    val total: Int
)

/** A store's location, from stores-service; the app never measures with it. */
@Serializable
data class StoreLocation(
    val latitude: Double,
    val longitude: Double,
    @SerialName("geofence_radius_m") val geofenceRadiusM: Int
)

/** `Store` of contracts/stores/openapi.yaml. */
@Serializable
data class Store(
    @SerialName("store_id") val storeId: Int,
    val name: String,
    val city: String,
    val state: String,
    @SerialName("is_active") val isActive: Boolean,
    val location: StoreLocation? = null
)

/** `StorePage` of contracts/stores/openapi.yaml. */
@Serializable
data class StorePage(
    val stores: List<Store>,
    @SerialName("page_info") val pageInfo: PageInfo
)

/** `CheckInRequest` of contracts/check-ins/openapi.yaml: a fix is all three or none. */
@Serializable
data class CheckInRequest(
    @SerialName("client_check_in_id") val clientCheckInId: String,
    @SerialName("store_id") val storeId: Int,
    @SerialName("occurred_at") val occurredAt: String,
    val latitude: Double? = null,
    val longitude: Double? = null,
    @SerialName("accuracy_m") val accuracyM: Double? = null
)

/** The fields of `CheckIn` the app shows; the server decides every one of them. */
@Serializable
data class CheckInResult(
    @SerialName("check_in_id") val checkInId: String,
    @SerialName("client_check_in_id") val clientCheckInId: String,
    @SerialName("store_id") val storeId: Int,
    val reason: String,
    @SerialName("distance_m") val distanceM: Double? = null,
    @SerialName("accuracy_m") val accuracyM: Double? = null,
    @SerialName("auto_verified") val autoVerified: Boolean,
    val verified: Boolean
)
