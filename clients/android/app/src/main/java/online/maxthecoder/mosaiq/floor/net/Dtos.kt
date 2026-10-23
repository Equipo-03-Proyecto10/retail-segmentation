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
    @SerialName("correlation_id") val correlationId: String? = null,
    val details: List<ErrorDetail> = emptyList()
)

@Serializable
data class ErrorDetail(val field: String, val code: String)

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

/** A product as products-service returns it; names and prices stay there (ADR-0035). */
@Serializable
data class Product(
    @SerialName("product_id") val productId: Int,
    val sku: String,
    val name: String,
    @SerialName("category_id") val categoryId: Int,
    @SerialName("list_price") val listPrice: String,
    @SerialName("is_active") val isActive: Boolean
)

@Serializable
data class ProductPage(val products: List<Product>, @SerialName("page_info") val pageInfo: PageInfo)

@Serializable
data class Customer(
    @SerialName("customer_id") val customerId: String,
    val name: String,
    val email: String?,
    val phone: String?,
    @SerialName("registration_channel_id") val registrationChannelId: Int,
    @SerialName("registered_on") val registeredOn: String
)

@Serializable
data class CustomerPage(val customers: List<Customer>, @SerialName("page_info") val pageInfo: PageInfo)

@Serializable
data class RegisterCustomerRequest(
    val name: String,
    val email: String?,
    val phone: String?,
    @SerialName("registration_channel_id") val registrationChannelId: Int
)

@Serializable
data class Channel(@SerialName("channel_id") val channelId: Int, val name: String)

@Serializable
data class ChannelList(val channels: List<Channel>)

@Serializable
data class Category(
    @SerialName("category_id") val categoryId: Int,
    val name: String,
    @SerialName("parent_category_id") val parentCategoryId: Int? = null
)

@Serializable
data class CategoryPage(val categories: List<Category>, @SerialName("page_info") val pageInfo: PageInfo)

@Serializable
data class PreferenceProfile(
    @SerialName("customer_id") val customerId: String,
    @SerialName("preferred_channel_ids") val preferredChannelIds: List<Int>,
    @SerialName("interest_category_ids") val interestCategoryIds: List<Int>
)

@Serializable
data class ReplaceInterestCategoriesRequest(@SerialName("category_ids") val categoryIds: List<Int>)

@Serializable
data class SaleLineRequest(
    @SerialName("product_id") val productId: Int,
    val quantity: Int,
    @SerialName("unit_price") val unitPrice: String
)

@Serializable
data class SaleRequest(
    @SerialName("customer_id") val customerId: String,
    @SerialName("store_id") val storeId: Int,
    @SerialName("channel_id") val channelId: Int,
    @SerialName("occurred_at") val occurredAt: String,
    val lines: List<SaleLineRequest>
)

@Serializable
data class Sale(
    @SerialName("sale_id") val saleId: Long,
    @SerialName("customer_id") val customerId: String,
    @SerialName("store_id") val storeId: Int,
    @SerialName("channel_id") val channelId: Int,
    @SerialName("occurred_at") val occurredAt: String,
    val total: String,
    val lines: List<SaleLineRequest>
)

@Serializable
data class SalePage(val sales: List<Sale>, @SerialName("page_info") val pageInfo: PageInfo)

@Serializable
data class StockLevel(
    @SerialName("store_id") val storeId: Int,
    @SerialName("product_id") val productId: Int,
    @SerialName("quantity_on_hand") val quantityOnHand: Int,
    @SerialName("updated_at") val updatedAt: String
)

/**
 * One engagement event (contracts/engagement/openapi.yaml). `clientEventId`
 * is fixed when the code is decoded, so every resend is the same event.
 */
@Serializable
data class EngagementEventRequest(
    @SerialName("client_event_id") val clientEventId: String,
    val type: String,
    @SerialName("store_id") val storeId: Int,
    @SerialName("product_id") val productId: Int,
    @SerialName("occurred_at") val occurredAt: String,
    @SerialName("customer_id") val customerId: String? = null,
    /** RECO_CLICK only: the assignment's label and the item's 1-based rank. */
    @SerialName("label_code") val labelCode: String? = null,
    val rank: Int? = null
)

/**
 * The customer's open assignment, exactly as segments-service states it:
 * `assigned`, `unassigned_result` or `no_open_assignment`. Nothing is filled in.
 */
@Serializable
data class CurrentAssignment(
    @SerialName("customer_id") val customerId: String,
    val state: String,
    @SerialName("label_code") val labelCode: String? = null,
    @SerialName("r_score") val rScore: Int? = null,
    @SerialName("f_score") val fScore: Int? = null,
    @SerialName("m_score") val mScore: Int? = null,
    val since: String? = null,
    @SerialName("run_id") val runId: Long? = null
)

@Serializable
data class RecommendationReason(
    val signal: String,
    @SerialName("segment_buyers") val segmentBuyers: Int? = null,
    @SerialName("category_id") val categoryId: Int? = null,
    val share: Double? = null
)

/** Ids and figures only; names come from products-service (ADR-0035). */
@Serializable
data class RecommendationItem(
    @SerialName("product_id") val productId: Int,
    @SerialName("category_id") val categoryId: Int,
    @SerialName("quantity_on_hand") val quantityOnHand: Int,
    val reasons: List<RecommendationReason>
)

@Serializable
data class RecommendationResult(
    @SerialName("customer_id") val customerId: String,
    val status: String,
    @SerialName("label_code") val labelCode: String? = null,
    @SerialName("usual_store_id") val usualStoreId: Int? = null,
    val items: List<RecommendationItem> = emptyList()
)

@Serializable
data class EngagementEvent(
    @SerialName("event_id") val eventId: String,
    @SerialName("client_event_id") val clientEventId: String,
    val type: String,
    @SerialName("store_id") val storeId: Int,
    @SerialName("product_id") val productId: Int,
    @SerialName("customer_id") val customerId: String? = null,
    @SerialName("label_code") val labelCode: String? = null,
    val rank: Int? = null,
    val voided: Boolean = false
)

@Serializable
data class EngagementEventPage(
    @SerialName("engagement_events") val events: List<EngagementEvent>,
    @SerialName("page_info") val pageInfo: PageInfo
)
