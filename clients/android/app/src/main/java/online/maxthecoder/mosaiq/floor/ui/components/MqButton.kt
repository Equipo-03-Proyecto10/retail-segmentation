package online.maxthecoder.mosaiq.floor.ui.components

import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonColors
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing
import online.maxthecoder.mosaiq.floor.ui.theme.mosaic

/** The four button looks of the web `mq-btn`: one primary per screen, then the rest. */
enum class MqButtonVariant { Primary, Secondary, Quiet, Danger }

/**
 * The MOSAIQ button. At least 48dp tall (never under the 44dp touch target). While
 * [loading] it shows a spinner, ignores taps and keeps its colors, so it does not
 * look disabled; [enabled] = false dims it.
 */
@Composable
fun MqButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    variant: MqButtonVariant = MqButtonVariant.Primary,
    enabled: Boolean = true,
    loading: Boolean = false,
    @DrawableRes leadingIcon: Int? = null,
    fullWidth: Boolean = false
) {
    val scheme = MaterialTheme.colorScheme
    val sized =
        modifier
            .then(if (fullWidth) Modifier.fillMaxWidth() else Modifier)
            .defaultMinSize(minWidth = ControlHeight, minHeight = ControlHeight)
            .semantics { if (loading) stateDescription = "Loading" }
    val active = enabled && !loading
    val shape = MaterialTheme.shapes.small
    val content: @Composable () -> Unit = {
        Row(
            horizontalArrangement = Arrangement.spacedBy(Spacing.Sm),
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (loading) {
                CircularProgressIndicator(
                    modifier = Modifier.defaultMinSize(Spacing.Xl, Spacing.Xl),
                    color = LocalContentColor.current,
                    strokeWidth = Spacing.Xxs
                )
            } else if (leadingIcon != null) {
                MqIcon(leadingIcon, contentDescription = null)
            }
            Text(text, style = MaterialTheme.typography.labelLarge)
        }
    }
    when (variant) {
        MqButtonVariant.Primary ->
            Button(
                onClick,
                sized,
                active,
                shape,
                colors = held(scheme.primary, scheme.onPrimary, loading),
                content = { content() }
            )
        MqButtonVariant.Danger ->
            Button(
                onClick,
                sized,
                active,
                shape,
                colors = held(scheme.error, scheme.onError, loading),
                content = { content() }
            )
        MqButtonVariant.Secondary ->
            OutlinedButton(
                onClick,
                sized,
                active,
                shape,
                colors = held(Color.Transparent, scheme.onSurface, loading),
                border = BorderStroke(Hairline, if (enabled) scheme.outline else scheme.outlineVariant),
                content = { content() }
            )
        MqButtonVariant.Quiet ->
            TextButton(
                onClick,
                sized,
                active,
                shape,
                colors = held(Color.Transparent, MaterialTheme.mosaic.link, loading),
                content = { content() }
            )
    }
}

/** While loading, the disabled colors equal the enabled ones. */
@Composable
private fun held(container: Color, content: Color, loading: Boolean): ButtonColors {
    val base = ButtonDefaults.buttonColors(containerColor = container, contentColor = content)
    return if (loading) base.copy(disabledContainerColor = container, disabledContentColor = content) else base
}
