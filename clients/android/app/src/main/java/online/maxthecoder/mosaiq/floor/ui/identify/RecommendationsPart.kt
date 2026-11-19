package online.maxthecoder.mosaiq.floor.ui.identify

import androidx.compose.foundation.layout.Column
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.LayoutCoordinates
import androidx.compose.ui.layout.boundsInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.ExplainedItem
import online.maxthecoder.mosaiq.floor.ui.ExplainedState
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqSection
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** At least this share of a row's height on screen before it counts as shown. */
const val SHOWN_FRACTION = 0.5f

/**
 * Whether a laid-out element is really on screen: at least [SHOWN_FRACTION] of its height
 * inside the window, after every scrolling or clipping parent has cut it.
 */
fun LayoutCoordinates.isShown(): Boolean {
    if (!isAttached || size.height <= 0) return false
    return boundsInWindow().height >= size.height * SHOWN_FRACTION
}

/**
 * The customer's recommendations at the checked-in store (F27-06): each item with where it
 * can be had and why it is offered, an alternative or another store's stock marked as such,
 * and the service's own reason when nothing is offered. [onShown] is called whenever a row is
 * laid out on screen, as often as that happens; the caller counts each item once. [onTap] is
 * null for a role that records nothing, and then a row is not a button.
 */
@Composable
fun RecommendationsPart(
    state: ExplainedState,
    onRetry: () -> Unit,
    onShown: (ExplainedItem) -> Unit,
    onTap: ((ExplainedItem) -> Unit)?,
    modifier: Modifier = Modifier
) {
    MqSection(stringResource(R.string.reco_title), modifier.testTag("profile-recommendations")) {
        val error = state.error
        when {
            state.noStore -> Note(stringResource(R.string.reco_no_store), Modifier.testTag("reco-no-store"))
            state.loading -> MqLoading(stringResource(R.string.reco_loading))
            error != null -> MqErrorPanel(error, onRetry = onRetry)
            else -> state.whyNone?.let { Note(it, Modifier.testTag("reco-why-none")) }
        }
        Column {
            state.items.forEachIndexed { index, shown ->
                RecommendationRow(shown, divider = index < state.items.lastIndex, onShown, onTap)
            }
        }
        state.tapNote?.let {
            Text(
                it,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }
            )
        }
        if (state.result != null || error != null) {
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
private fun RecommendationRow(
    shown: ExplainedItem,
    divider: Boolean,
    onShown: (ExplainedItem) -> Unit,
    onTap: ((ExplainedItem) -> Unit)?
) {
    val report by rememberUpdatedState(onShown)
    val tap = onTap?.let { { it(shown) } }
    MqListRow(
        headline = stringResource(R.string.insights_rank_and_name, shown.rank, shown.name),
        supporting = (listOf(shown.availability) + listOfNotNull(shown.note) + shown.reasons).joinToString("\n"),
        onClick = tap,
        divider = divider,
        horizontalPadding = Spacing.None,
        belowSupporting =
        when {
            shown.alternative -> {
                { MqBadge(stringResource(R.string.reco_kind_alternative), tone = MqTone.Info) }
            }
            shown.elsewhere -> {
                { MqBadge(stringResource(R.string.reco_kind_elsewhere), tone = MqTone.Warning) }
            }
            else -> null
        },
        modifier =
        Modifier
            .testTag("reco-${shown.rank}")
            .onGloballyPositioned { if (it.isShown()) report(shown) }
    )
}

@Composable
private fun Note(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier
    )
}
