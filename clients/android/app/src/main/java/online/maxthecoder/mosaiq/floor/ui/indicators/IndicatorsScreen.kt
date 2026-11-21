package online.maxthecoder.mosaiq.floor.ui.indicators

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.math.BigDecimal
import java.math.RoundingMode
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.KpiMeasure
import online.maxthecoder.mosaiq.floor.net.KpiReport
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqSection
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.formatDate
import online.maxthecoder.mosaiq.floor.ui.formatMoney
import online.maxthecoder.mosaiq.floor.ui.humanize
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** The store manager's figures for the chosen store, or for every store assigned when none is chosen. */
@Composable
fun IndicatorsScreen(model: IndicatorsViewModel, store: ChosenStore?) {
    val period by model.period.collectAsStateWithLifecycle()
    val state by model.state.collectAsStateWithLifecycle()
    LaunchedEffect(store?.storeId, period) { model.load(store) }
    IndicatorsContent(state, period, store?.name, onPeriod = model::choose, onRetry = { model.load(store) })
}

/** A figure's meaning, as the tap on it shows it. */
private data class Meaning(val title: String, val text: String)

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun IndicatorsContent(
    state: IndicatorsState,
    period: IndicatorPeriod,
    storeName: String?,
    onPeriod: (IndicatorPeriod) -> Unit,
    onRetry: () -> Unit
) {
    var title by rememberSaveable { mutableStateOf<String?>(null) }
    var text by rememberSaveable { mutableStateOf("") }
    val explain: (Meaning) -> Unit = {
        title = it.title
        text = it.text
    }
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(Spacing.Lg)
    ) {
        Text(
            stringResource(R.string.indicators_title),
            style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.semantics { heading() }
        )
        Text(
            storeName ?: stringResource(R.string.indicators_assigned_stores),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        FlowRow(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
            IndicatorPeriod.entries.forEach { choice ->
                FilterChip(
                    selected = choice == period,
                    onClick = { onPeriod(choice) },
                    label = { Text(stringResource(R.string.indicators_period_days, choice.days)) },
                    modifier = Modifier.testTag("period-${choice.days}")
                )
            }
        }
        when (state) {
            IndicatorsState.Loading -> MqLoading(stringResource(R.string.indicators_loading))
            is IndicatorsState.StoreRefused ->
                MqErrorPanel(
                    state.error,
                    friendly = stringResource(R.string.indicators_store_refused),
                    modifier = Modifier.testTag("store-refused")
                )
            is IndicatorsState.Shown -> Figures(state.indicators, explain, onRetry)
        }
    }
    title?.let { shown ->
        AlertDialog(
            onDismissRequest = { title = null },
            title = { Text(shown) },
            text = { Text(text) },
            confirmButton = {
                TextButton(onClick = { title = null }) { Text(stringResource(R.string.indicators_close)) }
            },
            modifier = Modifier.testTag("definition")
        )
    }
}

@Composable
private fun Figures(indicators: Indicators, explain: (Meaning) -> Unit, onRetry: () -> Unit) {
    Text(
        stringResource(
            R.string.indicators_window,
            formatDate(indicators.from.toString()),
            formatDate(indicators.to.toString())
        ),
        style = MaterialTheme.typography.bodySmall
    )
    val reports =
        listOf(
            R.string.indicators_sales to indicators.sales,
            R.string.indicators_conversion to indicators.conversion,
            R.string.indicators_customers to indicators.customers
        )
    reports.forEach { (heading, answer) ->
        MqSection(stringResource(heading)) { Report(answer, indicators, explain, onRetry) }
    }
    val unavailable = stringResource(R.string.indicators_definition_unavailable)
    val promotionsTitle = stringResource(R.string.indicators_campaigns)
    val promotionsMeaning = stringResource(R.string.indicators_campaigns_definition)
    MqSection(promotionsTitle) {
        when (val answer = indicators.campaigns) {
            is Answer.Refused -> MqErrorPanel(answer.error, onRetry = onRetry)
            is Answer.Body -> {
                val promotions = answer.value.promotions
                if (promotions.isEmpty()) Text(stringResource(R.string.indicators_campaigns_none))
                promotions.forEach { promotion ->
                    MqListRow(
                        headline = promotion.name,
                        supporting =
                        stringResource(
                            R.string.indicators_campaign_line,
                            promotion.discountPercent,
                            formatDate(promotion.startsOn),
                            formatDate(promotion.endsOn)
                        ),
                        onClick = { explain(Meaning(promotionsTitle, promotionsMeaning)) },
                        horizontalPadding = Spacing.None
                    )
                }
            }
        }
    }
    val stockTitle = stringResource(R.string.indicators_stock)
    MqSection(stockTitle) {
        when (val answer = indicators.stock) {
            is Answer.Refused -> MqErrorPanel(answer.error, onRetry = onRetry)
            is Answer.Body -> {
                val meaning = Meaning(stockTitle, answer.value.calculation.ifBlank { unavailable })
                if (answer.value.dataOrigin.synthetic) Synthetic()
                if (indicators.stockAlerts.isEmpty()) Text(stringResource(R.string.indicators_stock_none))
                indicators.stockAlerts.forEach { row ->
                    MqListRow(
                        headline =
                        indicators.categoryNames[row.categoryId]
                            ?: stringResource(R.string.indicators_category, row.categoryId),
                        supporting =
                        stringResource(
                            R.string.indicators_stock_line,
                            row.onHand,
                            row.daysOfCover ?: stringResource(R.string.indicators_not_computable),
                            row.replenishmentUnits
                        ),
                        onClick = { explain(meaning) },
                        horizontalPadding = Spacing.None
                    )
                }
            }
        }
    }
}

/** One ungrouped report: each measure of its single row, tappable for its definition. */
@Composable
private fun Report(answer: Answer<KpiReport>, indicators: Indicators, explain: (Meaning) -> Unit, onRetry: () -> Unit) {
    when (answer) {
        is Answer.Refused -> MqErrorPanel(answer.error, onRetry = onRetry)
        is Answer.Body -> {
            val report = answer.value
            if (report.dataOrigin.synthetic) Synthetic()
            val measures = report.rows.firstOrNull()?.measures.orEmpty()
            if (measures.isEmpty()) Text(stringResource(R.string.indicators_no_figures))
            val catalog = indicators.definition(report.kpi)
            val unavailable = stringResource(R.string.indicators_definition_unavailable)
            measures.forEach { measure ->
                val meaning =
                    catalog?.let { entry ->
                        val own = entry.measures.firstOrNull { it.code == measure.code }?.definition
                        stringResource(
                            R.string.indicators_definition,
                            own ?: unavailable,
                            entry.name,
                            entry.definition,
                            entry.window
                        )
                    } ?: unavailable
                MqListRow(
                    headline = humanize(measure.code),
                    onClick = { explain(Meaning(humanize(measure.code), meaning)) },
                    trailing = {
                        Text(measureValue(measure), style = MaterialTheme.typography.titleMedium)
                    },
                    horizontalPadding = Spacing.None,
                    modifier = Modifier.testTag("figure-${report.kpi}-${measure.code}")
                )
            }
        }
    }
}

@Composable
private fun Synthetic() {
    MqBadge(stringResource(R.string.indicators_synthetic), tone = MqTone.Warning)
}

/** A measure as its unit reads: money, a percentage, or a count; a figure that cannot be computed says so. */
@Composable
private fun measureValue(measure: KpiMeasure): String {
    val value = measure.value ?: return stringResource(R.string.indicators_not_computable)
    return when (measure.unit) {
        "currency" -> formatMoney(value)
        "ratio" -> stringResource(R.string.indicators_percent, ratioAsPercent(value))
        else -> value
    }
}

/** "0.1234" as "12.3"; text that is not a number is kept as is. */
internal fun ratioAsPercent(ratio: String): String {
    val number = ratio.trim().toBigDecimalOrNull() ?: return ratio
    return number.multiply(BigDecimal(PERCENT)).setScale(1, RoundingMode.HALF_UP).toPlainString()
}

private const val PERCENT = 100
