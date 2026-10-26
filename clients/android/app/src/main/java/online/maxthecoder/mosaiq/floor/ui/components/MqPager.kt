package online.maxthecoder.mosaiq.floor.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
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
        MqButton("Previous", onPrevious, variant = MqButtonVariant.Quiet, enabled = page > 1)
        Text(
            "Page $page of $pageCount",
            modifier = Modifier.weight(1f),
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.bodyMedium
        )
        MqButton("Next", onNext, variant = MqButtonVariant.Quiet, enabled = page < pageCount)
    }
}

/** The button under a list that appends the next batch; it shows a spinner while that loads. */
@Composable
fun MqLoadMore(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    loading: Boolean = false,
    label: String = "Load more"
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
