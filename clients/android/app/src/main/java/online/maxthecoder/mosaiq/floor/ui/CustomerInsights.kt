package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.CurrentAssignment
import online.maxthecoder.mosaiq.floor.net.RecommendationItem
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqCard
import online.maxthecoder.mosaiq.floor.ui.components.MqEmptyState
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing
import online.maxthecoder.mosaiq.floor.ui.theme.mosaic

/**
 * The selected customer's segment, as segments-service states it, and their
 * recommendations with reasons (F17-05). Tapping one opens the product
 * and counts one RECO_CLICK.
 */
@Composable
fun CustomerInsights(model: CustomerInsightsViewModel, onOpenProduct: (Int) -> Unit) {
    val state by model.state.collectAsStateWithLifecycle()
    CustomerInsightsContent(
        state = state,
        onRetry = { state.customerId?.let(model::load) },
        onOpen = { shown -> model.open(shown, openProduct = onOpenProduct) }
    )
}

/** The segment label in words, or null when the customer has no assigned segment. */
fun InsightsState.segmentLabel(): String? = assignment?.takeIf { it.state == ASSIGNED }?.labelCode?.let(::humanize)

/**
 * A segment as a badge. The dot takes one of the eight categorical colors, chosen from
 * the label by a stable hash so the same segment always looks the same; the label
 * text is always there, so color is never the only signal.
 */
@Composable
fun SegmentBadge(label: String, modifier: Modifier = Modifier) {
    val colors = MaterialTheme.mosaic.segment
    val color = colors[Math.floorMod(label.lowercase().hashCode(), colors.size)]
    Row(
        modifier,
        horizontalArrangement = Arrangement.spacedBy(Spacing.Sm),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.size(Spacing.Md).background(color, CircleShape))
        MqBadge(label)
    }
}

@Composable
fun CustomerInsightsContent(
    state: InsightsState,
    onRetry: () -> Unit,
    onOpen: (ShownRecommendation) -> Unit,
    modifier: Modifier = Modifier
) {
    if (state.customerId == null) return
    MqCard(modifier) {
        SegmentPart(state, onRetry)
        if (!state.loading) {
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            RecommendationsBlock(state, onRetry, onOpen)
        }
    }
}

@Composable
private fun SegmentPart(state: InsightsState, onRetry: () -> Unit) {
    val assignment = state.assignment
    val error = state.assignmentError
    Text(
        stringResource(R.string.insights_segment_title),
        style = MaterialTheme.typography.titleMedium,
        modifier = Modifier.semantics { heading() }
    )
    when {
        state.loading -> MqLoading(stringResource(R.string.insights_loading))
        error != null -> MqErrorPanel(error, onRetry = onRetry)
        assignment != null && state.segmentLabel() != null -> {
            SegmentBadge(state.segmentLabel().orEmpty(), Modifier.testTag("segment"))
            explanation(assignment)?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
        }
        else ->
            MqEmptyState(
                icon = R.drawable.ic_lucide_user_search,
                title = stringResource(R.string.insights_no_segment),
                message = assignment?.let { noSegmentReason(it) },
                modifier = Modifier.testTag("segment-empty")
            )
    }
}

@Composable
private fun explanation(assignment: CurrentAssignment): String? {
    val scores = listOfNotNull(assignment.rScore, assignment.fScore, assignment.mScore)
    if (scores.size < SCORE_COUNT) return null
    val line =
        stringResource(R.string.insights_scores, assignment.rScore ?: 0, assignment.fScore ?: 0, assignment.mScore ?: 0)
    val since = assignment.since?.let { stringResource(R.string.insights_since, formatDate(it)) }
    return listOfNotNull(line, since).joinToString(" ")
}

@Composable
private fun noSegmentReason(assignment: CurrentAssignment): String? = when (assignment.state) {
    "unassigned_result" -> stringResource(R.string.insights_unassigned)
    "no_open_assignment" -> stringResource(R.string.insights_not_run)
    else -> null
}

@Composable
private fun RecommendationsBlock(state: InsightsState, onRetry: () -> Unit, onOpen: (ShownRecommendation) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.Xs)) {
        Text(
            stringResource(R.string.insights_recommendations_title),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        val result = state.recommendations
        val error = state.recommendationsError
        when {
            error != null -> MqErrorPanel(error, onRetry = onRetry)
            result != null && state.items.isEmpty() ->
                Text(
                    CustomerInsightsViewModel.whyNone(result.status),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("why-none")
                )
        }
        state.items.forEach { shown -> RecommendationRow(shown, onOpen) }
        state.clickNote?.let {
            Text(
                it,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.error
            )
        }
        if (error == null && result != null) {
            MqButton(
                stringResource(R.string.insights_refresh),
                onRetry,
                variant = MqButtonVariant.Quiet,
                leadingIcon = R.drawable.ic_lucide_refresh_cw
            )
        }
    }
}

@Composable
private fun RecommendationRow(shown: ShownRecommendation, onOpen: (ShownRecommendation) -> Unit) {
    val description = stringResource(R.string.insights_open_product, shown.name)
    val onHand = stringResource(R.string.insights_on_hand, shown.item.quantityOnHand)
    MqListRow(
        headline = "${shown.rank}. ${shown.name}",
        supporting = (listOf(onHand) + shown.reasons).joinToString("\n"),
        chevron = true,
        divider = true,
        horizontalPadding = 0.dp,
        modifier =
        Modifier
            .testTag("recommendation-${shown.rank}")
            .semantics(mergeDescendants = true) { contentDescription = description }
            .clickable(role = Role.Button) { onOpen(shown) }
    )
}

private const val ASSIGNED = "assigned"
private const val SCORE_COUNT = 3

private fun previewState(assignment: CurrentAssignment?, items: Int = 2) = InsightsState(
    customerId = "c1",
    assignment = assignment,
    items =
    (1..items).map {
        ShownRecommendation(
            it,
            RecommendationItem(productId = it, categoryId = 1, quantityOnHand = 4 * it, reasons = emptyList()),
            "Trail socks $it",
            listOf("3 other customers in the same segment bought it")
        )
    }
)

@Composable
private fun InsightsPreview(dark: Boolean) = MosaiqTheme(darkTheme = dark) {
    Column(Modifier.padding(Spacing.Lg), verticalArrangement = Arrangement.spacedBy(Spacing.Lg)) {
        CustomerInsightsContent(
            previewState(CurrentAssignment("c1", ASSIGNED, "loyal_regulars", 4, 3, 5, "2026-03-12T09:00:00Z")),
            onRetry = {},
            onOpen = {}
        )
        CustomerInsightsContent(
            previewState(CurrentAssignment("c1", "no_open_assignment"), items = 0),
            onRetry = {},
            onOpen = {}
        )
    }
}

@Preview(name = "Insights, light", widthDp = 375, heightDp = 900, showBackground = true)
@Composable
private fun InsightsLight() = InsightsPreview(dark = false)

@Preview(name = "Insights, dark", widthDp = 375, heightDp = 900, showBackground = true)
@Composable
private fun InsightsDark() = InsightsPreview(dark = true)
