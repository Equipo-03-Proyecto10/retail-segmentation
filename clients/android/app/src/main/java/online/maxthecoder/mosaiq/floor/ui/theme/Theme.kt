package online.maxthecoder.mosaiq.floor.ui.theme

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

/** The accessor for the roles Material3 lacks: `MaterialTheme.mosaic.success`. */
val MaterialTheme.mosaic: MosaiqColors
    @Composable
    get() = LocalMosaiqColors.current

/**
 * The one theme of the app (ADR-0057): the MOSAIQ tokens on Material3, following
 * the system light or dark setting. Dynamic color is off on purpose, so the brand
 * does not change with the wallpaper.
 */
@Composable
fun MosaiqTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val colors = if (darkTheme) MosaiqDarkColorScheme else MosaiqLightColorScheme
    val extras = if (darkTheme) MosaiqDarkExtras else MosaiqLightExtras

    // Edge-to-edge draws the app under transparent bars: dark icons on the light
    // canvas, light icons on the dark one.
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = view.context.findActivity()?.window ?: return@SideEffect
            val controller = WindowCompat.getInsetsController(window, view)
            controller.isAppearanceLightStatusBars = !darkTheme
            controller.isAppearanceLightNavigationBars = !darkTheme
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                window.isNavigationBarContrastEnforced = false
            }
        }
    }

    CompositionLocalProvider(LocalMosaiqColors provides extras) {
        MaterialTheme(
            colorScheme = colors,
            typography = MosaiqTypography,
            shapes = MosaiqShapes,
            content = content
        )
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}
