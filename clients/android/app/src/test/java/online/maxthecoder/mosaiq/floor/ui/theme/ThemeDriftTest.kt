package online.maxthecoder.mosaiq.floor.ui.theme

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** ADR-0057 Compliance 1: a Color constant that differs from the token it claims fails here. */
class ThemeDriftTest {
    @Test
    fun everyPaletteColorEqualsItsTokenInTheCss() {
        val drift =
            MosaiqPalette.mapNotNull { (key, color) ->
                val scope = key.substringBefore('/')
                val token = key.substringAfter('/')
                // Primitives live in :root only; a dark scope falls back to it.
                val expected =
                    if (scope == "dark") TokenFiles.dark[token] ?: TokenFiles.light[token] else TokenFiles.light[token]
                val actual = TokenFiles.hex(color)
                when {
                    expected == null -> "$key is not a token in resolved-tokens.css"
                    expected != actual -> "$key: kotlin $actual, css $expected"
                    else -> null
                }
            }
        assertTrue("palette drifted from resolved-tokens.css:\n" + drift.joinToString("\n"), drift.isEmpty())
    }

    @Test
    fun theWholePaletteIsParsed() {
        assertTrue(MosaiqPalette.size > 60)
        assertTrue(TokenFiles.light.size > 100)
        assertTrue(TokenFiles.dark.size > 30)
    }

    @Test
    fun theColorSchemesAreBuiltFromPaletteTokens() {
        val l = MosaiqLightColorScheme
        val d = MosaiqDarkColorScheme
        fun check(role: String, actual: androidx.compose.ui.graphics.Color, scope: String, token: String) =
            assertEquals(role, MosaiqPalette["$scope/$token"], actual)
        check("light background", l.background, "light", "--mq-bg-canvas")
        check("light surface", l.surface, "light", "--mq-bg-surface")
        check("light onSurface", l.onSurface, "light", "--mq-text-primary")
        check("light primary", l.primary, "light", "--mq-accent")
        check("light onPrimary", l.onPrimary, "light", "--mq-accent-on")
        check("light outline", l.outline, "light", "--mq-neutral-500")
        check("light error", l.error, "light", "--mq-danger")
        check("dark background", d.background, "dark", "--mq-bg-canvas")
        check("dark surface", d.surface, "dark", "--mq-bg-surface")
        check("dark onSurface", d.onSurface, "dark", "--mq-text-primary")
        check("dark primary", d.primary, "dark", "--mq-accent")
        check("dark onPrimary", d.onPrimary, "dark", "--mq-accent-on")
        check("dark outline", d.outline, "light", "--mq-neutral-500")
        check("dark error", d.error, "dark", "--mq-danger")
        assertEquals(MosaiqPalette["light/--mq-text-link"], MosaiqLightExtras.link)
        assertEquals(MosaiqPalette["dark/--mq-text-link"], MosaiqDarkExtras.link)
        assertEquals(8, MosaiqLightExtras.segment.size)
        assertEquals(8, MosaiqDarkExtras.segment.size)
    }

    @Test
    fun theWindowAndLauncherResourcesUseTheTokens() {
        fun xmlColor(path: String, name: String): String {
            val text = java.io.File(TokenFiles.androidRes, path).readText()
            return Regex("""<color name="$name">(#[0-9A-Fa-f]{6})</color>""").find(text)!!.groupValues[1].uppercase()
        }
        assertEquals(TokenFiles.light["--mq-bg-canvas"], xmlColor("values/colors.xml", "mosaiq_canvas"))
        assertEquals(TokenFiles.dark["--mq-bg-canvas"], xmlColor("values-night/colors.xml", "mosaiq_canvas"))
        assertEquals(TokenFiles.light["--mq-neutral-950"], xmlColor("values/colors.xml", "mosaiq_neutral_950"))
        assertEquals(TokenFiles.light["--mq-white"], xmlColor("values/colors.xml", "mosaiq_white"))
    }
}
