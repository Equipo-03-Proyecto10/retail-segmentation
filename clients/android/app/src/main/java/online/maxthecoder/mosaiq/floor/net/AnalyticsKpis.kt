package online.maxthecoder.mosaiq.floor.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.Query

/**
 * analytics-kpis-service's reports (contracts/analytics-kpis/openapi.yaml). Every report is
 * aggregate: no report, row or field carries a customer identifier, and none is modelled here.
 * A store-scoped role naming a store it is not assigned to is refused with 403 `forbidden_store`.
 */
interface AnalyticsKpisApi {
    @GET(Services.ANALYTICS_KPIS + "kpi-reports/net-sales")
    suspend fun getNetSalesReport(
        @Query("from") from: String,
        @Query("to") to: String,
        @Query("store_id") storeId: Int?
    ): Response<KpiReport>

    @GET(Services.ANALYTICS_KPIS + "kpi-reports/spend")
    suspend fun getSpendReport(
        @Query("from") from: String,
        @Query("to") to: String,
        @Query("store_id") storeId: Int?
    ): Response<KpiReport>

    @GET(Services.ANALYTICS_KPIS + "kpi-reports/conversion")
    suspend fun getConversionReport(
        @Query("from") from: String,
        @Query("to") to: String,
        @Query("store_id") storeId: Int?
    ): Response<KpiReport>

    @GET(Services.ANALYTICS_KPIS + "kpi-reports/demand-coverage")
    suspend fun getDemandCoverageReport(
        @Query("from") from: String,
        @Query("to") to: String,
        @Query("store_id") storeId: Int?,
        @Query("size") size: Int = MAX_PAGE_SIZE
    ): Response<DemandCoverageReport>

    @GET(Services.ANALYTICS_KPIS + "kpi-reports/definitions")
    suspend fun listKpiDefinitions(@Query("size") size: Int = MAX_PAGE_SIZE): Response<KpiDefinitions>
}

/** `size` maximum of the contract's pages. */
const val MAX_PAGE_SIZE = 100

/** `Measure`: `value` is a decimal string, null when the figure cannot be computed. */
@Serializable
data class KpiMeasure(
    val code: String,
    val kind: String,
    val unit: String,
    val value: String? = null
)

/** `Row`, as far as an ungrouped report needs it. */
@Serializable
data class KpiRow(
    @SerialName("store_id") val storeId: Int? = null,
    val synthetic: Boolean = false,
    val measures: List<KpiMeasure>
)

@Serializable
data class DataOrigin(val views: List<String> = emptyList(), val synthetic: Boolean = false)

@Serializable
data class StoreScope(val restricted: Boolean, @SerialName("store_ids") val storeIds: List<Int> = emptyList())

/** `KpiReport`. */
@Serializable
data class KpiReport(
    val kpi: String,
    @SerialName("definition_version") val definitionVersion: Int,
    val from: String,
    val to: String,
    @SerialName("generated_at") val generatedAt: String,
    @SerialName("store_scope") val storeScope: StoreScope,
    @SerialName("data_origin") val dataOrigin: DataOrigin,
    val rows: List<KpiRow>
)

/** `DemandCoverageRow`; its segment split is demand only and is not read here. */
@Serializable
data class DemandCoverageRow(
    @SerialName("store_id") val storeId: Int,
    @SerialName("category_id") val categoryId: Int,
    @SerialName("net_units") val netUnits: Int,
    @SerialName("on_hand") val onHand: Int,
    @SerialName("days_of_cover") val daysOfCover: String? = null,
    @SerialName("replenishment_units") val replenishmentUnits: Int
)

/** `DemandCoverageReport`: `calculation` states its figures in words. */
@Serializable
data class DemandCoverageReport(
    val from: String,
    val to: String,
    @SerialName("target_cover_days") val targetCoverDays: Int,
    val calculation: String,
    @SerialName("data_origin") val dataOrigin: DataOrigin,
    val rows: List<DemandCoverageRow>
)

@Serializable
data class MeasureDefinition(val code: String, val definition: String)

/** `KpiDefinition`: the one statement of what a figure means. */
@Serializable
data class KpiDefinition(
    @SerialName("kpi_code") val kpiCode: String,
    val name: String,
    val version: Int,
    val definition: String,
    val window: String,
    val measures: List<MeasureDefinition>
)

@Serializable
data class KpiDefinitions(@SerialName("kpi_definitions") val kpiDefinitions: List<KpiDefinition>)
