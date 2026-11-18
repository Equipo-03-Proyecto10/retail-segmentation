package online.maxthecoder.mosaiq.floor.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.Query

/**
 * campaigns-service's promotions (contracts/campaigns/openapi.yaml). A floor role reads active
 * promotions only, and only for its assigned stores: another store is 403 `forbidden_store`.
 */
interface CampaignsApi {
    @GET(Services.CAMPAIGNS + "promotions")
    suspend fun listPromotions(
        @Query("store_id") storeId: Int?,
        @Query("active") active: Boolean = true,
        @Query("page") page: Int = 1,
        @Query("size") size: Int = 20
    ): Response<PromotionPage>
}

/** `Promotion`. */
@Serializable
data class Promotion(
    @SerialName("promotion_id") val promotionId: Long,
    @SerialName("campaign_id") val campaignId: Long,
    val name: String,
    @SerialName("discount_percent") val discountPercent: String,
    @SerialName("starts_on") val startsOn: String,
    @SerialName("ends_on") val endsOn: String,
    @SerialName("is_active") val isActive: Boolean
)

/** `Promotions`. */
@Serializable
data class PromotionPage(val promotions: List<Promotion>, @SerialName("page_info") val pageInfo: PageInfo)
