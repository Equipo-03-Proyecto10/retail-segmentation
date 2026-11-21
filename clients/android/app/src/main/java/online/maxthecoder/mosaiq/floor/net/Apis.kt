package online.maxthecoder.mosaiq.floor.net

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
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

/** products-service's catalog read (contracts/products/openapi.yaml). */
interface ProductsApi {
    @GET(Services.PRODUCTS + "products")
    suspend fun listProducts(
        @Query("q") query: String,
        @Query("page") page: Int = 1,
        @Query("size") size: Int = 20
    ): Response<ProductPage>

    /** Names for a list of ids, in one call: at most 50 ids (contracts/products). */
    @GET(Services.PRODUCTS + "products")
    suspend fun listProductsByIds(@Query("ids") ids: String, @Query("size") size: Int = MAX_IDS): Response<ProductPage>

    @GET(Services.PRODUCTS + "products/{product_id}")
    suspend fun getProduct(@Path("product_id") productId: Int): Response<Product>
}

interface CustomersApi {
    @GET(Services.CUSTOMERS + "customers")
    suspend fun searchCustomers(
        @Query("q") query: String? = null,
        @Query("email") email: String? = null,
        @Query("page") page: Int = 1,
        @Query("size") size: Int = 20
    ): Response<CustomerPage>

    @POST(Services.CUSTOMERS + "customers")
    suspend fun registerCustomer(@Body request: RegisterCustomerRequest): Response<Customer>

    /** A floor role's search: `q` of at least 3 characters, at most 20 per page. */
    @GET(Services.CUSTOMERS + "customers/floor-search")
    suspend fun searchFloorCustomers(
        @Query("q") query: String,
        @Query("page") page: Int = 1,
        @Query("size") size: Int = FLOOR_SEARCH_SIZE
    ): Response<FloorCustomerPage>

    @GET(Services.CUSTOMERS + "customers/{customer_id}/floor-view")
    suspend fun getFloorCustomer(@Path("customer_id") customerId: String): Response<FloorCustomer>

    @POST(Services.CUSTOMERS + "loyalty-cards/lookup")
    suspend fun lookupLoyaltyCard(@Body request: LookupLoyaltyCardRequest): Response<LoyaltyCardLookup>
}

interface CustomerPreferencesApi {
    @GET(Services.CUSTOMER_PREFERENCES + "preference-profiles/{customer_id}")
    suspend fun getProfile(@Path("customer_id") customerId: String): Response<PreferenceProfile>

    @GET(Services.CUSTOMER_PREFERENCES + "preference-profiles/{customer_id}/interests")
    suspend fun getInterestProfile(@Path("customer_id") customerId: String): Response<InterestProfile>

    @GET(Services.CUSTOMER_PREFERENCES + "preference-profiles/{customer_id}/restrictions")
    suspend fun getRestrictionProfile(@Path("customer_id") customerId: String): Response<RestrictionProfile>

    @PUT(Services.CUSTOMER_PREFERENCES + "preference-profiles/{customer_id}/interest-categories")
    suspend fun replaceInterests(
        @Path("customer_id") customerId: String,
        @Body request: ReplaceInterestCategoriesRequest
    ): Response<PreferenceProfile>

    @PUT(Services.CUSTOMER_PREFERENCES + "preference-profiles/{customer_id}/preferred-channels")
    suspend fun replacePreferredChannels(
        @Path("customer_id") customerId: String,
        @Body request: ReplacePreferredChannelsRequest
    ): Response<PreferenceProfile>

    @GET(Services.CUSTOMER_PREFERENCES + "preference-profiles/{customer_id}/consent")
    suspend fun getConsentState(@Path("customer_id") customerId: String): Response<ConsentState>

    /** Appends one decision per entry; answers 201 with the three purposes after the append. */
    @POST(Services.CUSTOMER_PREFERENCES + "preference-profiles/{customer_id}/consent-decisions")
    suspend fun recordConsentDecisions(
        @Path("customer_id") customerId: String,
        @Body request: RecordConsentDecisionsRequest
    ): Response<ConsentState>

    /** The consent log, newest first. Each read is an audited history read (ADR-0066 section 4). */
    @GET(Services.CUSTOMER_PREFERENCES + "preference-profiles/{customer_id}/consent-decisions")
    suspend fun listConsentDecisions(
        @Path("customer_id") customerId: String,
        @Query("page") page: Int = 1,
        @Query("size") size: Int = 20
    ): Response<ConsentDecisions>

    /** Each replace of the two declared sets, newest first. */
    @GET(Services.CUSTOMER_PREFERENCES + "preference-profiles/{customer_id}/preference-changes")
    suspend fun listPreferenceChanges(
        @Path("customer_id") customerId: String,
        @Query("page") page: Int = 1,
        @Query("size") size: Int = 20
    ): Response<PreferenceChanges>
}

interface CategoriesApi {
    @GET(Services.CATEGORIES + "categories")
    suspend fun listCategories(@Query("page") page: Int = 1, @Query("size") size: Int = 100): Response<CategoryPage>
}

interface SalesApi {
    @GET(Services.SALES + "channels")
    suspend fun listChannels(): Response<ChannelList>

    @GET(Services.SALES + "sales/{sale_id}")
    suspend fun getSale(@Path("sale_id") saleId: Long): Response<Sale>

    @GET(Services.SALES + "sales")
    suspend fun listSales(
        @Query("customer_id") customerId: String,
        @Query("page") page: Int = 1,
        @Query("size") size: Int = 20
    ): Response<SalePage>
}

/**
 * stock-service's level, keyed by its own identity (E-03). Read uncached, so it
 * reflects the latest sale (contracts/stock/openapi.yaml).
 */
interface StockApi {
    @GET(Services.STOCK + "stock-levels/{store_id}/{product_id}")
    suspend fun getStockLevel(@Path("store_id") storeId: Int, @Path("product_id") productId: Int): Response<StockLevel>
}

/** The largest page `customers/floor-search` serves (contracts/customers). */
const val FLOOR_SEARCH_SIZE = 20

/** At most this many product ids per `products?ids=` call. */
const val MAX_IDS = 50

/** segments-service's current assignment (contracts/segments/openapi.yaml). */
interface SegmentsApi {
    @GET(Services.SEGMENTS + "segment-assignments/current")
    suspend fun getCurrentAssignment(@Query("customer_id") customerId: String): Response<CurrentAssignment>

    /** The floor roles' read: the label and `since`, never the scores (ADR-0060, F21-26). */
    @GET(Services.SEGMENTS + "segment-assignments/floor-view")
    suspend fun getFloorCurrentAssignment(@Query("customer_id") customerId: String): Response<FloorCurrentAssignment>
}

/** recommendations-service's RN-40 answer (contracts/recommendations/openapi.yaml). */
interface RecommendationsApi {
    @GET(Services.RECOMMENDATIONS + "recommendations")
    suspend fun getRecommendations(
        @Query("customer_id") customerId: String,
        @Query("limit") limit: Int = 10
    ): Response<RecommendationResult>
}

/** engagement-service's event list (contracts/engagement/openapi.yaml). */
interface EngagementApi {
    @GET(Services.ENGAGEMENT + "engagement-events")
    suspend fun listEvents(
        @Query("type") type: String? = null,
        @Query("customer_id") customerId: String? = null,
        @Query("page") page: Int = 1,
        @Query("size") size: Int = 20
    ): Response<EngagementEventPage>
}

/** campaigns-service's promotions; a floor role reads active ones of its own stores only (contracts/campaigns). */
interface CampaignsApi {
    @GET(Services.CAMPAIGNS + "promotions")
    suspend fun listPromotions(
        @Query("store_id") storeId: Int,
        @Query("channel_id") channelId: Int,
        @Query("active") active: Boolean = true,
        @Query("page") page: Int = 1,
        @Query("size") size: Int = MAX_PAGE_SIZE
    ): Response<PromotionPage>
}

/** experimentation-service's arm of one customer in one campaign; reading it records nothing. */
interface ExperimentationApi {
    @GET(Services.EXPERIMENTATION + "experiment-assignments/current")
    suspend fun getCurrentAssignment(
        @Query("customer_id") customerId: String,
        @Query("campaign_id") campaignId: Int
    ): Response<ExperimentAssignment>
}

/** The largest page a contract's `size` allows. */
const val MAX_PAGE_SIZE = 100
