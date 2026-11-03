package online.maxthecoder.mosaiq.floor.ui.theme

import androidx.compose.material3.ColorScheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

// The MOSAIQ tokens (docs/design-system/tokens/resolved-tokens.css) as Compose
// colors, mapped onto Material3 roles as ADR-0057 writes. Each constant is named
// after the token it copies; ThemeDriftTest fails when one differs from the CSS.

// Primitives, theme-invariant.
private val Neutral050 = Color(0xFFFAFAF9)
private val Neutral100 = Color(0xFFF4F4F2)
private val Neutral200 = Color(0xFFE7E7E4)
private val Neutral400 = Color(0xFFB3B3AE)
private val Neutral500 = Color(0xFF8C8C86)
private val Neutral600 = Color(0xFF6B6B65)
private val Neutral700 = Color(0xFF4F4F4A)
private val Neutral800 = Color(0xFF383834)
private val Neutral900 = Color(0xFF232320)
private val Neutral950 = Color(0xFF141412)
private val White = Color(0xFFFFFFFF)
private val Black = Color(0xFF000000)
private val Orange500 = Color(0xFFFF6B00)
private val Orange600 = Color(0xFFD45400)
private val Orange700 = Color(0xFFA33F00)

// Semantic, light (:root).
private val LightBgCanvas = Color(0xFFFAFAF9)
private val LightBgSurface = Color(0xFFFFFFFF)
private val LightBgSunken = Color(0xFFF4F4F2)
private val LightBgActive = Color(0xFFE7E7E4)
private val LightBorderSubtle = Color(0xFFE7E7E4)
private val LightTextPrimary = Color(0xFF232320)
private val LightTextSecondary = Color(0xFF6B6B65)
private val LightTextInverse = Color(0xFFFAFAF9)
private val LightTextLink = Color(0xFFA33F00)
private val LightAccent = Color(0xFFD45400)
private val LightAccentOn = Color(0xFF000000)
private val LightAccentSubtle = Color(0xFFFFE8D1)
private val LightAccentSubtleOn = Color(0xFF5C2300)
private val LightSuccess = Color(0xFF1F7A4C)
private val LightWarning = Color(0xFFB26A00)
private val LightDanger = Color(0xFFC0342B)
private val LightDangerSubtle = Color(0xFFFAE4E2)
private val LightInfo = Color(0xFF1F5FA8)
private val LightInfoSubtle = Color(0xFFE2EBF6)

// Semantic, dark ([data-mq-theme="dark"]).
private val DarkBgCanvas = Color(0xFF0B0B0A)
private val DarkBgSurface = Color(0xFF141412)
private val DarkBgRaised = Color(0xFF232320)
private val DarkBgSunken = Color(0xFF0B0B0A)
private val DarkBgHover = Color(0xFF383834)
private val DarkBorderSubtle = Color(0xFF383834)
private val DarkTextPrimary = Color(0xFFF4F4F2)
private val DarkTextSecondary = Color(0xFFB3B3AE)
private val DarkTextInverse = Color(0xFF141412)
private val DarkTextLink = Color(0xFFFFB566)
private val DarkAccent = Color(0xFFFF6B00)
private val DarkAccentOn = Color(0xFF000000)
private val DarkAccentSubtle = Color(0xFF3A1D00)
private val DarkAccentSubtleOn = Color(0xFFFFB566)
private val DarkSuccess = Color(0xFF4FBF85)
private val DarkWarning = Color(0xFFE0A44A)
private val DarkDanger = Color(0xFFE8736A)
private val DarkDangerSubtle = Color(0xFF361715)
private val DarkInfo = Color(0xFF6BA6E8)
private val DarkInfoSubtle = Color(0xFF122436)

// Categorical segment colors cat-01..08: Paul Tol muted, and Tol light in dark.
private val LightSegments =
    listOf(
        Color(0xFF332288),
        Color(0xFFCC6677),
        Color(0xFF44AA99),
        Color(0xFF117733),
        Color(0xFFAA4499),
        Color(0xFFDDCC77),
        Color(0xFF88CCEE),
        Color(0xFF999933)
    )
private val DarkSegments =
    listOf(
        Color(0xFF77AADD),
        Color(0xFFEE8866),
        Color(0xFF44BB99),
        Color(0xFFBBCC33),
        Color(0xFFFFAABB),
        Color(0xFFEEDD88),
        Color(0xFF99DDFF),
        Color(0xFFAAAA00)
    )

/**
 * Every color constant above, keyed by the CSS custom property it claims to copy,
 * prefixed `light/` or `dark/` for the scope it is read in (primitives are
 * `light/` only). The drift test compares each entry with resolved-tokens.css.
 */
internal val MosaiqPalette: Map<String, Color> =
    buildMap {
        put("light/--mq-neutral-050", Neutral050)
        put("light/--mq-neutral-100", Neutral100)
        put("light/--mq-neutral-200", Neutral200)
        put("light/--mq-neutral-400", Neutral400)
        put("light/--mq-neutral-500", Neutral500)
        put("light/--mq-neutral-600", Neutral600)
        put("light/--mq-neutral-700", Neutral700)
        put("light/--mq-neutral-800", Neutral800)
        put("light/--mq-neutral-900", Neutral900)
        put("light/--mq-neutral-950", Neutral950)
        put("light/--mq-white", White)
        put("light/--mq-black", Black)
        put("light/--mq-orange-500", Orange500)
        put("light/--mq-orange-600", Orange600)
        put("light/--mq-orange-700", Orange700)

        put("light/--mq-bg-canvas", LightBgCanvas)
        put("light/--mq-bg-surface", LightBgSurface)
        put("light/--mq-bg-sunken", LightBgSunken)
        put("light/--mq-bg-active", LightBgActive)
        put("light/--mq-border-subtle", LightBorderSubtle)
        put("light/--mq-text-primary", LightTextPrimary)
        put("light/--mq-text-secondary", LightTextSecondary)
        put("light/--mq-text-inverse", LightTextInverse)
        put("light/--mq-text-link", LightTextLink)
        put("light/--mq-accent", LightAccent)
        put("light/--mq-accent-on", LightAccentOn)
        put("light/--mq-accent-subtle", LightAccentSubtle)
        put("light/--mq-accent-subtle-on", LightAccentSubtleOn)
        put("light/--mq-success", LightSuccess)
        put("light/--mq-warning", LightWarning)
        put("light/--mq-danger", LightDanger)
        put("light/--mq-danger-subtle", LightDangerSubtle)
        put("light/--mq-info", LightInfo)
        put("light/--mq-info-subtle", LightInfoSubtle)

        put("dark/--mq-bg-canvas", DarkBgCanvas)
        put("dark/--mq-bg-surface", DarkBgSurface)
        put("dark/--mq-bg-raised", DarkBgRaised)
        put("dark/--mq-bg-sunken", DarkBgSunken)
        put("dark/--mq-bg-hover", DarkBgHover)
        put("dark/--mq-border-subtle", DarkBorderSubtle)
        put("dark/--mq-text-primary", DarkTextPrimary)
        put("dark/--mq-text-secondary", DarkTextSecondary)
        put("dark/--mq-text-inverse", DarkTextInverse)
        put("dark/--mq-text-link", DarkTextLink)
        put("dark/--mq-accent", DarkAccent)
        put("dark/--mq-accent-on", DarkAccentOn)
        put("dark/--mq-accent-subtle", DarkAccentSubtle)
        put("dark/--mq-accent-subtle-on", DarkAccentSubtleOn)
        put("dark/--mq-success", DarkSuccess)
        put("dark/--mq-warning", DarkWarning)
        put("dark/--mq-danger", DarkDanger)
        put("dark/--mq-danger-subtle", DarkDangerSubtle)
        put("dark/--mq-info", DarkInfo)
        put("dark/--mq-info-subtle", DarkInfoSubtle)

        LightSegments.forEachIndexed { i, c -> put("light/--mq-cat-0${i + 1}", c) }
        DarkSegments.forEachIndexed { i, c -> put("dark/--mq-cat-0${i + 1}", c) }
    }

// Roles ADR-0057 does not list (secondary, tertiary, inverse, container surfaces)
// are filled from the same tokens so no stock Material3 purple is left behind.
internal val MosaiqLightColorScheme: ColorScheme =
    lightColorScheme(
        primary = LightAccent,
        onPrimary = LightAccentOn,
        primaryContainer = LightAccentSubtle,
        onPrimaryContainer = LightAccentSubtleOn,
        inversePrimary = Orange500,
        secondary = Neutral700,
        onSecondary = Neutral050,
        secondaryContainer = LightBgActive,
        onSecondaryContainer = LightTextPrimary,
        tertiary = LightInfo,
        onTertiary = White,
        tertiaryContainer = LightInfoSubtle,
        onTertiaryContainer = LightTextPrimary,
        background = LightBgCanvas,
        onBackground = LightTextPrimary,
        surface = LightBgSurface,
        onSurface = LightTextPrimary,
        surfaceVariant = LightBgSunken,
        onSurfaceVariant = LightTextSecondary,
        surfaceTint = Color.Transparent,
        inverseSurface = Neutral900,
        inverseOnSurface = LightTextInverse,
        error = LightDanger,
        onError = White,
        errorContainer = LightDangerSubtle,
        onErrorContainer = LightTextPrimary,
        outline = Neutral500,
        outlineVariant = LightBorderSubtle,
        scrim = Black,
        surfaceBright = LightBgSurface,
        surfaceDim = LightBgCanvas,
        surfaceContainerLowest = LightBgSurface,
        surfaceContainerLow = LightBgSurface,
        surfaceContainer = LightBgSunken,
        surfaceContainerHigh = LightBgActive,
        surfaceContainerHighest = LightBgActive
    )

internal val MosaiqDarkColorScheme: ColorScheme =
    darkColorScheme(
        primary = DarkAccent,
        onPrimary = DarkAccentOn,
        primaryContainer = DarkAccentSubtle,
        onPrimaryContainer = DarkAccentSubtleOn,
        inversePrimary = Orange600,
        secondary = Neutral400,
        onSecondary = Neutral950,
        secondaryContainer = DarkBgHover,
        onSecondaryContainer = DarkTextPrimary,
        tertiary = DarkInfo,
        onTertiary = Black,
        tertiaryContainer = DarkInfoSubtle,
        onTertiaryContainer = DarkTextPrimary,
        background = DarkBgCanvas,
        onBackground = DarkTextPrimary,
        surface = DarkBgSurface,
        onSurface = DarkTextPrimary,
        surfaceVariant = DarkBgSunken,
        onSurfaceVariant = DarkTextSecondary,
        surfaceTint = Color.Transparent,
        inverseSurface = DarkTextPrimary,
        inverseOnSurface = DarkTextInverse,
        error = DarkDanger,
        onError = Black,
        errorContainer = DarkDangerSubtle,
        onErrorContainer = DarkTextPrimary,
        outline = Neutral500,
        outlineVariant = DarkBorderSubtle,
        scrim = Black,
        surfaceBright = DarkBgSurface,
        surfaceDim = DarkBgCanvas,
        surfaceContainerLowest = DarkBgCanvas,
        surfaceContainerLow = DarkBgSurface,
        surfaceContainer = DarkBgRaised,
        surfaceContainerHigh = DarkBgHover,
        surfaceContainerHighest = DarkBgHover
    )

/**
 * The roles Material3 has no slot for: state colors, the categorical segment
 * colors, and the color of accent-colored text. A state or a segment is never
 * carried by color alone; it always has a text label next to it.
 */
@Immutable
data class MosaiqColors(
    val success: Color,
    val warning: Color,
    val info: Color,
    /** `cat-01..08`, in order; index with the segment's position, modulo 8. */
    val segment: List<Color>,
    /** Accent-colored text and links: orange-700 (light) or the dark link token, never `primary`. */
    val link: Color
)

internal val MosaiqLightExtras =
    MosaiqColors(LightSuccess, LightWarning, LightInfo, LightSegments, LightTextLink)

internal val MosaiqDarkExtras =
    MosaiqColors(DarkSuccess, DarkWarning, DarkInfo, DarkSegments, DarkTextLink)

internal val LocalMosaiqColors = staticCompositionLocalOf { MosaiqLightExtras }
