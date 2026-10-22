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
}

interface CustomerPreferencesApi {
    @GET(Services.CUSTOMER_PREFERENCES + "preference-profiles/{customer_id}")
    suspend fun getProfile(@Path("customer_id") customerId: String): Response<PreferenceProfile>

    @PUT(Services.CUSTOMER_PREFERENCES + "preference-profiles/{customer_id}/interest-categories")
    suspend fun replaceInterests(
        @Path("customer_id") customerId: String,
        @Body request: ReplaceInterestCategoriesRequest
    ): Response<PreferenceProfile>
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
