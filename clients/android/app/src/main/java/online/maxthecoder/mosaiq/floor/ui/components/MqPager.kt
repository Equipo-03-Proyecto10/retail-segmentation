package online.maxthecoder.mosaiq.floor.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * "Previous, Page x of y, Next". [page] counts from 1. Both buttons are 48dp
 * targets; the one that has nowhere to go is disabled, not hidden, so the row does not jump.
 */
@Composable
fun MqPager(page: Int, pageCount: Int, onPrevious: () -> Unit, onNext: () -> Unit, modifier: Modifier = Modifier) {
    val label: @Composable (Modifier) -> Unit = {
        Text(
            stringResource(R.string.component_page, page, pageCount),
            modifier = it.semantics { liveRegion = LiveRegionMode.Polite },
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.bodyMedium
        )
    }
    val previous: @Composable () -> Unit = {
        MqButton(stringResource(R.string.component_previous), onPrevious, variant = MqButtonVariant.Secondary, enabled = page > 1)
    }
    val next: @Composable () -> Unit = {
        MqButton(stringResource(R.string.component_next), onNext, variant = MqButtonVariant.Secondary, enabled = page < pageCount)
    }
    if (LocalDensity.current.fontScale > LARGE_FONT_SCALE) {
        // At large font sizes the three would squeeze each other: the label goes above the buttons.
        Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
            label(Modifier.fillMaxWidth())
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                previous()
                next()
            }
        }
    } else {
        Row(
            modifier = modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(Spacing.Sm),
            verticalAlignment = Alignment.CenterVertically
        ) {
            previous()
            label(Modifier.weight(1f))
            next()
        }
    }
}

private const val LARGE_FONT_SCALE = 1.3f

/** The button under a list that appends the next batch; it shows a spinner while that loads. */
@Composable
fun MqLoadMore(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    loading: Boolean = false,
    label: String = stringResource(R.string.component_load_more)
) {
    MqButton(
        label,
        onClick,
        modifier = modifier,
        variant = MqButtonVariant.Secondary,
        loading = loading,
        fullWidth = true
    )
}
