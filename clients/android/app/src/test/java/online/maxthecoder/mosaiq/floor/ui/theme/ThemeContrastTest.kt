package online.maxthecoder.mosaiq.floor.ui.theme

import androidx.compose.ui.graphics.Color
import kotlin.math.pow
import org.junit.Assert.assertTrue
import org.junit.Test

/** ADR-0057 Compliance 2: WCAG 2.1 contrast of the pairs the theme defines, in both schemes. */
class ThemeContrastTest {
    private fun channel(v: Float): Double = if (v <= 0.03928f) v / 12.92 else ((v + 0.055) / 1.055).pow(2.4)

    private fun luminance(c: Color) = 0.2126 * channel(c.red) + 0.7152 * channel(c.green) + 0.0722 * channel(c.blue)

    private fun ratio(a: Color, b: Color): Double {
        val (hi, lo) = luminance(a).let { x -> luminance(b).let { y -> maxOf(x, y) to minOf(x, y) } }
        return (hi + 0.05) / (lo + 0.05)
    }

    private class Pair(val name: String, val fg: Color, val bg: Color, val min: Double)

    private fun pairs(scheme: androidx.compose.material3.ColorScheme, extras: MosaiqColors): List<Pair> = with(scheme) {
        listOf(
            Pair("onSurface/surface", onSurface, surface, TEXT),
            Pair("onBackground/background", onBackground, background, TEXT),
            Pair("onSurface/surfaceVariant", onSurface, surfaceVariant, TEXT),
            Pair("onSurfaceVariant/background", onSurfaceVariant, background, TEXT),
            Pair("onSurfaceVariant/surface", onSurfaceVariant, surface, TEXT),
            Pair("onPrimary/primary", onPrimary, primary, TEXT),
            Pair("onPrimaryContainer/primaryContainer", onPrimaryContainer, primaryContainer, TEXT),
            Pair("onSecondary/secondary", onSecondary, secondary, TEXT),
            Pair("onSecondaryContainer/secondaryContainer", onSecondaryContainer, secondaryContainer, TEXT),
            Pair("onTertiary/tertiary", onTertiary, tertiary, TEXT),
            Pair("onError/error", onError, error, TEXT),
            Pair("onErrorContainer/errorContainer", onErrorContainer, errorContainer, TEXT),
            Pair("error/background (error text)", error, background, TEXT),
            Pair("error/surface (error text)", error, surface, TEXT),
            Pair("link/background (accent text)", extras.link, background, TEXT),
            Pair("link/surface (accent text)", extras.link, surface, TEXT),
            Pair("inverseOnSurface/inverseSurface", inverseOnSurface, inverseSurface, TEXT),
            Pair("outline/background (border)", outline, background, BORDER),
            Pair("outline/surface (border)", outline, surface, BORDER),
            Pair("primary/background (focus ring, filled control)", primary, background, BORDER)
        )
    }

    private fun failures(scheme: androidx.compose.material3.ColorScheme, extras: MosaiqColors) =
        pairs(scheme, extras).filter { ratio(it.fg, it.bg) < it.min }
            .map { "%s %.2f:1 < %.1f:1".format(it.name, ratio(it.fg, it.bg), it.min) }

    @Test
    fun lightSchemeMeetsWcag() {
        val bad = failures(MosaiqLightColorScheme, MosaiqLightExtras)
        assertTrue("light:\n" + bad.joinToString("\n"), bad.isEmpty())
    }

    @Test
    fun darkSchemeMeetsWcag() {
        val bad = failures(MosaiqDarkColorScheme, MosaiqDarkExtras)
        assertTrue("dark:\n" + bad.joinToString("\n"), bad.isEmpty())
    }

    @Test
    fun accentTextIsBlackOnTheAccentInBothSchemes() {
        // The issue's criterion: black on #D45400 (light) and black on #FF6B00 (dark).
        assertTrue(ratio(Color.Black, MosaiqLightColorScheme.primary) >= TEXT)
        assertTrue(ratio(Color.Black, MosaiqDarkColorScheme.primary) >= TEXT)
    }

    private companion object {
        const val TEXT = 4.5
        const val BORDER = 3.0
    }
}
