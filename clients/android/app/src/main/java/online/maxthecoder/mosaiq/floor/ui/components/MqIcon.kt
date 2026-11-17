package online.maxthecoder.mosaiq.floor.ui.components

import androidx.annotation.DrawableRes
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.Dp
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** The 1px rule of the web system: borders and dividers are hairlines, never shadows. */
internal val Hairline: Dp = Spacing.Xxs / 2

/** The height of a control the finger reaches for (the brief prefers 48dp over 44dp). */
internal val ControlHeight: Dp = Spacing.X5l

/** The shortest list row: 56dp. */
internal val RowHeight: Dp = Spacing.X5l + Spacing.Sm

/**
 * A Lucide icon vendored in `res/drawable` as `ic_lucide_<name>`. The drawable is
 * black; [tint] colors it, by default with the surrounding content color. Pass a
 * [contentDescription] only when the icon carries meaning the text next to it does not.
 */
@Composable
fun MqIcon(
    @DrawableRes id: Int,
    contentDescription: String?,
    modifier: Modifier = Modifier,
    tint: Color = LocalContentColor.current,
    size: Dp = Spacing.Xl
) {
    Icon(
        painter = painterResource(id),
        contentDescription = contentDescription,
        modifier = modifier.size(size),
        tint = tint
    )
}
