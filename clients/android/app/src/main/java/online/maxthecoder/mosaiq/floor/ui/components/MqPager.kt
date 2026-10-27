package online.maxthecoder.mosaiq.floor.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * "Previous, Page x of y, Next". [page] counts from 1. Both buttons are 48dp
 * targets; the one that has nowhere to go is disabled, not hidden, so the row does not jump.
 */
@Composable
fun MqPager(page: Int, pageCount: Int, onPrevious: () -> Unit, onNext: () -> Unit, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(Spacing.Sm),
        verticalAlignment = Alignment.CenterVertically
    ) {
        MqButton(
            stringResource(R.string.component_previous),
            onPrevious,
            variant = MqButtonVariant.Secondary,
            enabled = page > 1
        )
        Text(
            stringResource(R.string.component_page, page, pageCount),
            modifier = Modifier.weight(1f),
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.bodyMedium
        )
        MqButton(
            stringResource(R.string.component_next),
            onNext,
            variant = MqButtonVariant.Secondary,
            enabled = page < pageCount
        )
    }
}

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
