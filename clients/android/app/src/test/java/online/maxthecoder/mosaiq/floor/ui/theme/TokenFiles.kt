package online.maxthecoder.mosaiq.floor.ui.theme

import androidx.compose.ui.graphics.Color
import java.io.File

/** Finds the repository files from wherever Gradle runs the tests (clients/android/app, usually). */
internal object TokenFiles {
    val repoRoot: File =
        generateSequence(File("").absoluteFile) { it.parentFile }
            .firstOrNull { File(it, "docs/design-system/tokens/resolved-tokens.css").isFile }
            ?: error("docs/design-system/tokens/resolved-tokens.css not found above ${File("").absolutePath}")

    val resolvedTokens = File(repoRoot, "docs/design-system/tokens/resolved-tokens.css")
    val androidRes = File(repoRoot, "clients/android/app/src/main/res")

    /** Custom properties of the block that opens with [selector], as `--name` to `#RRGGBB`. */
    fun block(selector: String): Map<String, String> {
        val css = resolvedTokens.readText()
        val start = css.indexOf("$selector{")
        require(start >= 0) { "$selector block not found" }
        val end = css.indexOf("\n}", start)
        val body = css.substring(start, end)
        return Regex("""(--mq-[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\s*;""")
            .findAll(body)
            .associate { it.groupValues[1] to it.groupValues[2].uppercase() }
    }

    val light: Map<String, String> by lazy { block(":root") }
    val dark: Map<String, String> by lazy { block("[data-mq-theme=\"dark\"]") }

    fun hex(color: Color): String = "#%06X".format(color.value.shr(32).toInt() and 0xFFFFFF)
}
