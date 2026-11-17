package online.maxthecoder.mosaiq.floor.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import online.maxthecoder.mosaiq.floor.R

@OptIn(ExperimentalTextApi::class)
private fun sans(weight: Int) = Font(
    R.font.ibm_plex_sans_variable,
    weight = FontWeight(weight),
    variationSettings = FontVariation.Settings(FontVariation.weight(weight))
)

/** IBM Plex Sans, one variable font file, for body and UI text. */
val PlexSans =
    FontFamily(sans(400), sans(500), sans(600), sans(700))

/** IBM Plex Mono, for identifiers (customer, product and store ids). */
val PlexMono =
    FontFamily(
        Font(R.font.ibm_plex_mono_400, FontWeight.Normal),
        Font(R.font.ibm_plex_mono_500, FontWeight.Medium),
        Font(R.font.ibm_plex_mono_600, FontWeight.SemiBold)
    )

// Tabular figures on every style, so numbers line up in columns (ADR-0057).
private const val TABULAR = "tnum"

private fun style(size: Int, line: Int, weight: Int, family: FontFamily = PlexSans) = TextStyle(
    fontFamily = family,
    fontWeight = FontWeight(weight),
    fontSize = size.sp,
    lineHeight = line.sp,
    letterSpacing = 0.sp,
    fontFeatureSettings = TABULAR
)

/** The ADR-0057 table; styles it does not list take the nearest listed size. */
val MosaiqTypography =
    Typography(
        displayLarge = style(32, 40, 700),
        displayMedium = style(32, 40, 700),
        displaySmall = style(32, 40, 700),
        headlineLarge = style(32, 40, 700),
        headlineMedium = style(32, 40, 700),
        headlineSmall = style(24, 32, 600),
        titleLarge = style(20, 28, 600),
        titleMedium = style(16, 24, 600),
        titleSmall = style(14, 20, 600),
        bodyLarge = style(16, 24, 400),
        bodyMedium = style(14, 20, 400),
        bodySmall = style(12, 16, 400),
        labelLarge = style(14, 20, 600),
        labelMedium = style(12, 16, 500),
        labelSmall = style(12, 16, 500)
    )

/** Identifiers: body size in Plex Mono. */
val MosaiqIdStyle: TextStyle = style(14, 20, 400, PlexMono)
