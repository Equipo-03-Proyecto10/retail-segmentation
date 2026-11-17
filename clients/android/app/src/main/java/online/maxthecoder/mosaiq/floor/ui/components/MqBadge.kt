package online.maxthecoder.mosaiq.floor.ui.components

import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing
import online.maxthecoder.mosaiq.floor.ui.theme.mosaic

/** The badge tones of the web `mq-badge`. */
enum class MqTone { Neutral, Accent, Success, Warning, Danger, Info }

/** How much of a state color tints its badge background (success and warning have no subtle token). */
private const val SUBTLE_ALPHA = 0.14f

/**
 * A status label. It always carries text, so color is never the only signal; the
 * text stays in the primary text color for contrast and the tone shows in the tint,
 * the hairline border and the optional [leadingIcon].
 */
@Composable
fun MqBadge(
    text: String,
    modifier: Modifier = Modifier,
    tone: MqTone = MqTone.Neutral,
    @DrawableRes leadingIcon: Int? = null
) {
    val scheme = MaterialTheme.colorScheme
    val extras = MaterialTheme.mosaic
    val (strong, container, content) =
        when (tone) {
            MqTone.Neutral -> Triple(scheme.outline, scheme.surfaceContainerHigh, scheme.onSurface)
            MqTone.Accent -> Triple(scheme.primary, scheme.primaryContainer, scheme.onPrimaryContainer)
            MqTone.Success -> Triple(extras.success, extras.success.subtle(), scheme.onSurface)
            MqTone.Warning -> Triple(extras.warning, extras.warning.subtle(), scheme.onSurface)
            MqTone.Danger -> Triple(scheme.error, scheme.errorContainer, scheme.onErrorContainer)
            MqTone.Info -> Triple(extras.info, scheme.tertiaryContainer, scheme.onTertiaryContainer)
        }
    Surface(
        modifier = modifier,
        shape = MaterialTheme.shapes.extraSmall,
        color = container,
        contentColor = content,
        border = BorderStroke(Hairline, strong)
    ) {
        Row(
            modifier = Modifier.padding(horizontal = Spacing.Sm, vertical = Spacing.Xs),
            horizontalArrangement = Arrangement.spacedBy(Spacing.Xs),
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (leadingIcon != null) {
                MqIcon(leadingIcon, contentDescription = null, tint = strong, size = Spacing.Lg)
            }
            Text(text, style = MaterialTheme.typography.labelMedium)
        }
    }
}

private fun Color.subtle(): Color = copy(alpha = SUBTLE_ALPHA)
