package online.maxthecoder.mosaiq.floor.ui.components

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.Dp
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * A list row at least 56dp tall. With [onClick] the whole row is the target and
 * announces itself as a button; without, it is plain content. [chevron] marks a row
 * that opens something. [divider] draws a hairline under it.
 */
@Composable
fun MqListRow(
    headline: String,
    modifier: Modifier = Modifier,
    supporting: String? = null,
    onClick: (() -> Unit)? = null,
    enabled: Boolean = true,
    chevron: Boolean = false,
    divider: Boolean = false,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
    belowSupporting: (@Composable () -> Unit)? = null,
    horizontalPadding: Dp = Spacing.Lg
) {
    Column(modifier) {
        Row(
            modifier =
            Modifier
                .fillMaxWidth()
                .then(
                    if (onClick != null) {
                        Modifier.clickable(enabled = enabled, role = Role.Button, onClick = onClick)
                    } else {
                        Modifier
                    }
                )
                .heightIn(min = RowHeight)
                .padding(horizontal = horizontalPadding, vertical = Spacing.Sm),
            horizontalArrangement = Arrangement.spacedBy(Spacing.Md),
            verticalAlignment = Alignment.CenterVertically
        ) {
            leading?.invoke()
            Column(Modifier.weight(1f)) {
                Text(headline, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurface)
                if (supporting != null) {
                    Text(
                        supporting,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                belowSupporting?.let {
                    Box(Modifier.padding(top = Spacing.Xs)) { it() }
                }
            }
            trailing?.invoke()
            if (chevron) {
                MqIcon(
                    R.drawable.ic_lucide_chevron_right,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
        if (divider) HorizontalDivider(thickness = Hairline, color = MaterialTheme.colorScheme.outlineVariant)
    }
}
