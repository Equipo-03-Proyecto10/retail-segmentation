package online.maxthecoder.mosaiq.floor.ui.indicators

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.time.Clock
import java.time.LocalDate
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.AppGraph
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.DemandCoverageReport
import online.maxthecoder.mosaiq.floor.net.DemandCoverageRow
import online.maxthecoder.mosaiq.floor.net.KpiDefinition
import online.maxthecoder.mosaiq.floor.net.KpiDefinitions
import online.maxthecoder.mosaiq.floor.net.KpiReport
import online.maxthecoder.mosaiq.floor.net.PromotionPage
import online.maxthecoder.mosaiq.floor.net.call
import online.maxthecoder.mosaiq.floor.session.ChosenStore

/** The periods a manager chooses from; each is whole UTC days. */
enum class IndicatorPeriod(val days: Int) { LAST_7(7), LAST_30(30), LAST_90(90) }

/**
 * The window of this period: [days] whole UTC days ending yesterday. A day still running would lower
 * daily demand, so demand coverage refuses it (`window_not_ended`); every figure uses the same window.
 */
fun IndicatorPeriod.window(today: LocalDate): Pair<LocalDate, LocalDate> {
    val to = today.minusDays(1)
    return to.minusDays(days - 1L) to to
}

/** Each figure's own answer, so one refused report leaves the others on screen. */
data class Indicators(
    val from: LocalDate,
    val to: LocalDate,
    val sales: Answer<KpiReport>,
    val conversion: Answer<KpiReport>,
    val customers: Answer<KpiReport>,
    val stock: Answer<DemandCoverageReport>,
    val campaigns: Answer<PromotionPage>,
    val definitions: Answer<KpiDefinitions>,
    val categoryNames: Map<Int, String>
) {
    /** The catalog entry of [kpi], or `null` when the catalog could not be read. */
    fun definition(kpi: String): KpiDefinition? =
        (definitions as? Answer.Body)?.value?.kpiDefinitions?.firstOrNull { it.kpiCode == kpi }

    /** Stock alerts: the categories whose stock does not cover the target days of demand. */
    val stockAlerts: List<DemandCoverageRow>
        get() = (stock as? Answer.Body)?.value?.rows?.filter { it.replenishmentUnits > 0 }.orEmpty()
}

sealed interface IndicatorsState {
    data object Loading : IndicatorsState

    /** The store is not one of the caller's: a service refused it with `forbidden_store` (ADR-0060). */
    data class StoreRefused(val error: ApiError) : IndicatorsState

    data class Shown(val indicators: Indicators) : IndicatorsState
}

/**
 * The store manager's figures (F27-08): local sales, conversion and aggregate customer results from
 * analytics-kpis, stock alerts from its demand coverage, and active campaigns as campaigns' active
 * promotions, each read at its own service. Only aggregates are read: no customer is shown.
 */
class IndicatorsViewModel(
    private val graph: AppGraph,
    private val clock: Clock = Clock.systemUTC()
) : ViewModel() {
    val period = MutableStateFlow(IndicatorPeriod.LAST_30)
    val state = MutableStateFlow<IndicatorsState>(IndicatorsState.Loading)
    private var loading: Job? = null

    init {
        // Another user's figures never stay on screen.
        viewModelScope.launch {
            graph.session.userId.collect {
                loading?.cancel()
                state.value = IndicatorsState.Loading
            }
        }
    }

    fun choose(next: IndicatorPeriod) {
        period.value = next
    }

    /** Reads every figure for [store], or for the caller's assigned stores when none is chosen. */
    fun load(store: ChosenStore?) {
        loading?.cancel()
        val user = graph.session.userId.value ?: return
        val (from, to) = period.value.window(LocalDate.now(clock))
        state.value = IndicatorsState.Loading
        loading =
            viewModelScope.launch {
                val kpis = graph.network.kpis
                val first = from.toString()
                val last = to.toString()
                val id = store?.storeId
                val sales = async { call { kpis.getNetSalesReport(first, last, id) } }
                val conversion = async { call { kpis.getConversionReport(first, last, id) } }
                val customers = async { call { kpis.getSpendReport(first, last, id) } }
                val stock = async { call { kpis.getDemandCoverageReport(first, last, id) } }
                val campaigns = async { call { graph.network.campaigns.listPromotions(id) } }
                val definitions = async { call { kpis.listKpiDefinitions() } }
                val categories = async { call { graph.network.categories.listCategories() } }
                val indicators =
                    Indicators(
                        from = from,
                        to = to,
                        sales = sales.await(),
                        conversion = conversion.await(),
                        customers = customers.await(),
                        stock = stock.await(),
                        campaigns = campaigns.await(),
                        definitions = definitions.await(),
                        categoryNames =
                        (categories.await() as? Answer.Body)?.value?.categories.orEmpty()
                            .associate { it.categoryId to it.name }
                    )
                if (graph.session.userId.value != user) return@launch
                // The store-scoped reads; a refusal of the chosen store by any of them refuses the screen.
                val storeReads =
                    listOf(
                        indicators.sales,
                        indicators.conversion,
                        indicators.customers,
                        indicators.stock,
                        indicators.campaigns
                    )
                val refused =
                    storeReads.filterIsInstance<Answer.Refused>().firstOrNull { it.error.code == FORBIDDEN_STORE }
                state.value =
                    refused?.let { IndicatorsState.StoreRefused(it.error) } ?: IndicatorsState.Shown(indicators)
            }
    }

    private companion object {
        const val FORBIDDEN_STORE = "forbidden_store"
    }
}
