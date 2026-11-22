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

/** `FloorCustomer` of contracts/customers: `email` and `phone` are absent for a seller (F21-13). */
@Serializable
data class FloorCustomer(
    @SerialName("customer_id") val customerId: String,
    val name: String,
    val email: String? = null,
    val phone: String? = null
)

@Serializable
data class FloorCustomerPage(val customers: List<FloorCustomer>, @SerialName("page_info") val pageInfo: PageInfo)

/** The scanned code travels in the body only, never in a URL (contracts/customers `lookupLoyaltyCard`). */
@Serializable
data class LookupLoyaltyCardRequest(val code: String)

/** Two identifiers and no personal data. */
@Serializable
data class LoyaltyCardLookup(
    @SerialName("card_id") val cardId: String,
    @SerialName("customer_id") val customerId: String
)

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

/** `ReplacePreferredChannelsRequest` of contracts/customer-preferences: the complete new set. */
@Serializable
data class ReplacePreferredChannelsRequest(@SerialName("channel_ids") val channelIds: List<Int>)

/**
 * One purpose's current state (`PurposeConsent`). `NOT_RECORDED` is never granted; for it every
 * member after [state] is null, and [recordedBy] is null for a `MIGRATION` decision too.
 */
@Serializable
data class PurposeConsent(
    val purpose: String,
    val state: String,
    @SerialName("decision_id") val decisionId: Long? = null,
    @SerialName("recorded_at") val recordedAt: String? = null,
    @SerialName("recorded_by") val recordedBy: String? = null,
    val source: String? = null,
    val basis: String? = null
)

/** `ConsentState`: the three purposes, in the contract's order. */
@Serializable
data class ConsentState(
    @SerialName("customer_id") val customerId: String,
    @SerialName("as_of") val asOf: String,
    val purposes: List<PurposeConsent>
)

/** `ConsentDecisionInput`: a purpose and a decision, nothing else; the service adds who, when and how. */
@Serializable
data class ConsentDecisionInput(val purpose: String, val decision: String)

@Serializable
data class RecordConsentDecisionsRequest(val decisions: List<ConsentDecisionInput>)

/** One row of the append-only consent log (`ConsentDecision`). */
@Serializable
data class ConsentDecision(
    @SerialName("decision_id") val decisionId: Long,
    @SerialName("customer_id") val customerId: String,
    val purpose: String,
    val decision: String,
    @SerialName("recorded_at") val recordedAt: String,
    @SerialName("recorded_by") val recordedBy: String? = null,
    val source: String,
    val basis: String
)

@Serializable
data class ConsentDecisions(
    @SerialName("consent_decisions") val decisions: List<ConsentDecision>,
    @SerialName("page_info") val pageInfo: PageInfo
)

/** One replace of one declared set (`PreferenceChange`), with the ids before and after. */
@Serializable
data class PreferenceChange(
    @SerialName("change_id") val changeId: Long,
    @SerialName("customer_id") val customerId: String,
    @SerialName("preference_set") val preferenceSet: String,
    @SerialName("before_ids") val beforeIds: List<Int>,
    @SerialName("after_ids") val afterIds: List<Int>,
    @SerialName("changed_at") val changedAt: String,
    @SerialName("changed_by") val changedBy: String
)

@Serializable
data class PreferenceChanges(
    @SerialName("preference_changes") val changes: List<PreferenceChange>,
    @SerialName("page_info") val pageInfo: PageInfo
)

/**
 * `SaleLineInput` of contracts/sales 1.9.0: `unitPrice` is the price actually charged, and
 * `promotionId` names the one promotion a discounted line relies on. A null is left out of the body.
 */
@Serializable
data class SaleLineRequest(
    @SerialName("product_id") val productId: Int,
    val quantity: Int,
    @SerialName("unit_price") val unitPrice: String,
    @SerialName("promotion_id") val promotionId: Int? = null
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

/** `StoreCommercialRules` of contracts/stores: a null cap means the store imposes none of its own. */
@Serializable
data class StoreCommercialRules(
    @SerialName("max_discount_percent") val maxDiscountPercent: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null
)

/**
 * A `CART_ADD` or `CART_REMOVE` of engagement v2 (`CartAddRequestV2`, `CartRemoveRequestV2`): intent,
 * never a sale. The client event id is fixed at the tap, so every resend is the same event.
 */
@Serializable
data class CartEventRequest(
    @SerialName("client_event_id") val clientEventId: String,
    val type: String,
    @SerialName("store_id") val storeId: Int,
    @SerialName("product_id") val productId: Int,
    val quantity: Int,
    @SerialName("occurred_at") val occurredAt: String,
    @SerialName("customer_id") val customerId: String? = null
)

@Serializable
data class StockLevel(
    @SerialName("store_id") val storeId: Int,
    @SerialName("product_id") val productId: Int,
    @SerialName("quantity_on_hand") val quantityOnHand: Int,
    @SerialName("updated_at") val updatedAt: String
)

/**
 * The fields of `ProductDetails` (contracts/products) the floor shows: the product
 * plus its commercial status, `active`, `paused` or `discontinued` (F21-09), and
 * its barcode, null when it has none, which confirms a barcode look-up (F27-04).
 */
@Serializable
data class ProductDetails(
    @SerialName("product_id") val productId: Int,
    val sku: String,
    val name: String,
    @SerialName("category_id") val categoryId: Int,
    @SerialName("list_price") val listPrice: String,
    @SerialName("is_active") val isActive: Boolean,
    @SerialName("commercial_status") val commercialStatus: String,
    val barcode: String? = null
) {
    val product: Product get() = Product(productId, sku, name, categoryId, listPrice, isActive)
}

/**
 * One `Availability` row of contracts/stock: a product in a store, in one channel.
 * `quantityOnHand` is null when the store holds no stock record for it.
 */
@Serializable
data class Availability(
    @SerialName("channel_id") val channelId: Int,
    @SerialName("is_available") val isAvailable: Boolean,
    val reason: String,
    @SerialName("quantity_on_hand") val quantityOnHand: Int? = null
)

@Serializable
data class AvailabilityPage(val availabilities: List<Availability>, @SerialName("page_info") val pageInfo: PageInfo)

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

/**
 * A customer's current segment as a floor role reads it (contracts/segments
 * `getFloorCurrentAssignment`, ADR-0060): the label and `since`, in the three states of
 * the full read. It declares no score and no `run_id`, so an element the service ever
 * sent beyond these is dropped when the body is parsed and never kept in memory.
 */
@Serializable
data class FloorCurrentAssignment(
    @SerialName("customer_id") val customerId: String,
    val state: String,
    @SerialName("label_code") val labelCode: String? = null,
    val since: String? = null
)

/** One inferred interest: the category and how strong it is, nothing about how it was measured. */
@Serializable
data class InferredInterest(
    @SerialName("category_id") val categoryId: Int,
    val strength: Int
)

/**
 * The customer's interests (contracts/customer-preferences `getInterestProfile`): what they
 * declared and what was inferred, kept apart. Inferred ones exist only while personalization
 * is granted.
 */
@Serializable
data class InterestProfile(
    @SerialName("customer_id") val customerId: String,
    @SerialName("declared_category_ids") val declaredCategoryIds: List<Int> = emptyList(),
    @SerialName("inferred_interests") val inferredInterests: List<InferredInterest> = emptyList()
)

/**
 * What the customer does not want done (contracts/customer-preferences
 * `getRestrictionProfile`). `personalizationAllowed` is the one effect of consent a floor
 * role reads; the consent record itself is not part of it.
 */
@Serializable
data class RestrictionProfile(
    @SerialName("customer_id") val customerId: String,
    @SerialName("personalization_allowed") val personalizationAllowed: Boolean,
    @SerialName("restricted_channel_ids") val restrictedChannelIds: List<Int> = emptyList(),
    @SerialName("restricted_category_ids") val restrictedCategoryIds: List<Int> = emptyList()
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

/** Where an item is available, and the stock figure the decision used (contracts/recommendations, `/v2`). */
@Serializable
data class ItemAvailability(
    @SerialName("store_id") val storeId: Int,
    @SerialName("channel_id") val channelId: Int? = null,
    @SerialName("quantity_on_hand") val quantityOnHand: Int
)

/**
 * One `/v2` item: `primary` (a recommendation available here), `alternative` (stands in for
 * [alternativeFor], unavailable for [unavailableReason]) or `elsewhere` (the product itself, held
 * by the store in [availability]). Ids and figures only; names come from their owners.
 */
@Serializable
data class RecommendationItemV2(
    @SerialName("product_id") val productId: Int,
    @SerialName("category_id") val categoryId: Int,
    val kind: String,
    val position: Int,
    val availability: ItemAvailability,
    @SerialName("alternative_for") val alternativeFor: Int? = null,
    @SerialName("unavailable_reason") val unavailableReason: String? = null,
    val reasons: List<RecommendationReason>
)

@Serializable
data class RecommendationExclusion(val reason: String, val count: Int)

/** recommendations' `/v2` answer, the elements this client reads. */
@Serializable
data class RecommendationV2(
    @SerialName("decision_id") val decisionId: String,
    @SerialName("customer_id") val customerId: String,
    val status: String,
    @SerialName("label_code") val labelCode: String? = null,
    @SerialName("store_id") val storeId: Int? = null,
    @SerialName("channel_id") val channelId: Int? = null,
    val exclusions: List<RecommendationExclusion> = emptyList(),
    val items: List<RecommendationItemV2> = emptyList()
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

/** `Promotion` of contracts/campaigns: the offer, its discount and validity, and where it applies. */
@Serializable
data class Promotion(
    @SerialName("promotion_id") val promotionId: Int,
    @SerialName("campaign_id") val campaignId: Int,
    val name: String,
    @SerialName("discount_percent") val discountPercent: String,
    @SerialName("starts_on") val startsOn: String,
    @SerialName("ends_on") val endsOn: String,
    @SerialName("store_ids") val storeIds: List<Int>,
    @SerialName("channel_ids") val channelIds: List<Int>,
    @SerialName("is_active") val isActive: Boolean
)

@Serializable
data class PromotionPage(val promotions: List<Promotion>, @SerialName("page_info") val pageInfo: PageInfo)

/** The fields of experimentation's `ExperimentAssignment` the floor reads: the arm, and whether it is the control. */
@Serializable
data class ExperimentAssignment(
    @SerialName("experiment_id") val experimentId: Long,
    @SerialName("arm_kind") val armKind: String,
    @SerialName("arm_name") val armName: String
) {
    val isControl: Boolean get() = armKind == "CONTROL"
}

/** `CampaignExposureRequestV2` of contracts/engagement: the customer was shown one campaign's offer. */
@Serializable
data class CampaignExposureRequest(
    @SerialName("client_event_id") val clientEventId: String,
    val type: String,
    @SerialName("store_id") val storeId: Int,
    @SerialName("campaign_id") val campaignId: Int,
    @SerialName("customer_id") val customerId: String,
    @SerialName("occurred_at") val occurredAt: String
)

/**
 * `CaptureIncidentRequest` of contracts/engagement (ADR-0071): a queued write that was finally refused,
 * reported once. No customer field and no free text, by contract.
 */
@Serializable
data class CaptureIncidentRequest(
    @SerialName("client_incident_id") val clientIncidentId: String,
    @SerialName("source_type") val sourceType: String,
    @SerialName("source_operation_id") val sourceOperationId: String,
    @SerialName("source_correlation_id") val sourceCorrelationId: String,
    @SerialName("store_id") val storeId: Int,
    @SerialName("refused_status") val refusedStatus: Int,
    @SerialName("refused_code") val refusedCode: String,
    val attempts: Int,
    @SerialName("occurred_at") val occurredAt: String,
    @SerialName("line_count") val lineCount: Int? = null
)

/**
 * One review of an incident (`IncidentReview`): the state it moved to, the supervisor's note and when. `by` is
 * left out on purpose: no person's id is kept on the phone.
 */
@Serializable
data class IncidentReview(
    val status: String,
    val note: String? = null,
    val at: String
)

/**
 * The fields of `CaptureIncidentItem` the phone reads. `reported_by` and the reviewers' ids are left out on
 * purpose: nothing about a person reaches the phone. [history] is what the supervisor's review screen shows;
 * the updates screen ignores it.
 */
@Serializable
data class CaptureIncident(
    @SerialName("incident_id") val incidentId: String,
    @SerialName("source_type") val sourceType: String,
    @SerialName("store_id") val storeId: Int,
    @SerialName("refused_status") val refusedStatus: Int,
    @SerialName("refused_code") val refusedCode: String,
    @SerialName("occurred_at") val occurredAt: String,
    @SerialName("recorded_at") val recordedAt: String,
    val status: String,
    val attempts: Int? = null,
    @SerialName("line_count") val lineCount: Int? = null,
    val history: List<IncidentReview> = emptyList()
)

/** `CaptureIncidentReviewRequest`: the state to move to, and an optional note of at most 200 characters. */
@Serializable
data class ReviewCaptureIncidentRequest(val status: String, val note: String? = null)

@Serializable
data class CaptureIncidentPage(
    @SerialName("capture_incidents") val captureIncidents: List<CaptureIncident>,
    @SerialName("page_info") val pageInfo: PageInfo
)

/**
 * A `CAMPAIGN_EXPOSURE` of `/v2` as the review screen counts it: where and for which campaign a promotion was
 * shown. The event also carries a customer id, which this type does not declare, so it never reaches memory.
 */
@Serializable
data class ExposureEvent(
    val type: String,
    @SerialName("store_id") val storeId: Int,
    @SerialName("campaign_id") val campaignId: Int? = null
)

@Serializable
data class ExposureEventPage(
    @SerialName("engagement_events") val events: List<ExposureEvent>,
    @SerialName("page_info") val pageInfo: PageInfo
)
