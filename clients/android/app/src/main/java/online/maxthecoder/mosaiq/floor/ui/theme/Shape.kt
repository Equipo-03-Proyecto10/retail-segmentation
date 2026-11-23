package online.maxthecoder.mosaiq.floor.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** The web radii, 2, 4 and 6dp, with 6dp as the ceiling (ADR-0057). */
val MosaiqShapes =
    Shapes(
        extraSmall = RoundedCornerShape(2.dp),
        small = RoundedCornerShape(4.dp),
        medium = RoundedCornerShape(6.dp),
        large = RoundedCornerShape(6.dp),
        extraLarge = RoundedCornerShape(6.dp)
    )

/**
 * The only two elevations of ADR-0057, the popover and modal shadows of ADR-0002
 * (`0 2px 6px` and `0 8px 24px`), as Compose shadow elevations. There is no tonal elevation.
 */
object Elevation {
    val Popover: Dp = 3.dp
    val Modal: Dp = 8.dp
}
