package online.maxthecoder.mosaiq.floor

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * UX-12: no user-facing sentence lives in `ui/`. Text reaches the screen through `stringResource`,
 * `pluralStringResource` or [AppText]. Two rules fail the build: a string literal with a letter in a text-producing
 * position (an argument of `Text`, `Mq*`, `label =`, `contentDescription =` and the like), and, anywhere else, a
 * literal that reads as text, meaning a phrase (letters, a space, letters) or a capitalized word such as `"None"`. Codes
 * (`"physical_store"`), test tags, date patterns and preview names are not text. Money goes through `formatMoney`
 * and counts through `plurals`, see [UiStringGuardTest.noRawMoneyOrCountSuffixRemains].
 *
 * A line that must keep a literal ends with `// ui-string-ok: <reason>` (or has that comment alone on the line above); a file made only of previews
 * carries `// ui-string-ok-file: preview` once, and a file whose previews sit at the end carries
 * `// ui-string-ok-from: previews below` above them. `ui/theme/` is not scanned.
 */
class UiStringGuardTest {
    private val ui = File("src/main/java/online/maxthecoder/mosaiq/floor/ui")

    @Test
    fun noUserFacingLiteralRemainsInUi() {
        val files = ui.walkTopDown().filter {
            it.isFile && it.extension == "kt" && "theme" !in it.relativeTo(ui).path.split('/').first()
        }
        val found = files.toList().flatMap { file ->
            UiStringScan.violations(file.readText()).map {
                "${file.relativeTo(ui)}:${it.line}: ${it.text}"
            }
        }
        assertEquals(
            "move these to res/values/strings.xml or mark them // ui-string-ok: <reason>",
            emptyList<String>(),
            found
        )
    }

    @Test
    fun noRawMoneyOrCountSuffixRemains() {
        val files = ui.walkTopDown().filter { it.isFile && it.extension == "kt" && it.name != "Format.kt" }
        val found = files.toList().flatMap { file ->
            file.readLines().withIndex().filter { (_, line) -> UiStringScan.rawMoneyOrPluralSuffix(line) }
                .map { (index, line) -> "${file.relativeTo(ui)}:${index + 1}: ${line.trim()}" }
        }
        assertEquals(
            "show money with formatMoney and counts with a plurals resource",
            emptyList<String>(),
            found
        )
    }

    @Test
    fun catchesRawMoneyAndHandRolledPlurals() {
        listOf(
            """Text("${'$'}${'$'}{product.listPrice}")""",
            """Text("${'$'}" + price)""",
            """val label = count.toString() + if (count == 1) " item" else " items"""",
            """Text(if (n == 1) "one" else "many")"""
        ).forEach { assertTrue("not caught: $it", UiStringScan.rawMoneyOrPluralSuffix(it)) }
        listOf(
            """Text(formatMoney(product.listPrice))""",
            """if (page == 1) return""",
            """Text(pluralStringResource(R.plurals.x, n, n))"""
        ).forEach { assertTrue("wrongly caught: $it", !UiStringScan.rawMoneyOrPluralSuffix(it)) }
    }

    @Test
    fun theGuardScansTheScreens() {
        val scanned = ui.walkTopDown().count { it.isFile && it.extension == "kt" }
        assertTrue("ui sources were found", scanned > 20)
    }

    // The guard is only worth having if it fails on a real violation: these run it on snippets.

    @Test
    fun catchesTheCallShapesThatProduceText() {
        val bad =
            listOf(
                """Text("Hello")""",
                "Text(\n    \"Hello\",\n    style = x\n)",
                """Text("Total ${'$'}total")""",
                """Button(onClick = f) { Text("Go") }""",
                """MqButton("Next", onNext)""",
                """MqTextField(v, {}, label = "Email")""",
                """Icon(p, contentDescription = "Menu")""",
                """Icon(p, contentDescription = if (open) "Hide" else "Show")""",
                """MqTextField(v, {}, supportingText = "Type two letters.")""",
                """OutlinedTextField(placeholder = { Text("Search") })""",
                """val s = String.format("Page %d", n)""",
                """Foo(title = "Sale ${'$'}{x}")""",
                // Anywhere else a literal reads as text: a phrase, or a capitalized word.
                """const val UNREADABLE = "Check-in sent. The answer could not be read."""",
                """val label = name ?: "Unknown"""",
                """val rows = listOf("Alpha beta", "Gamma")""",
                """private val Heading = "Customers"""",
                """error = ApiError(0, "x", "Sale refused.", null)"""
            )
        for (snippet in bad) {
            assertTrue("not caught: $snippet", UiStringScan.violations(snippet).isNotEmpty())
        }
    }

    @Test
    fun letsResourcesCodesAndMarkedLinesThrough() {
        val fine =
            listOf(
                """Text(stringResource(R.string.x))""",
                """Text(text = pluralStringResource(R.plurals.x, n, n))""",
                """Modifier.testTag("nav-scan")""",
                """Text(formatMoney(price) + " ")""",
                """Text("${'$'}{a}: ${'$'}{b}")""",
                """Text("12:30")""",
                """label = fieldError(error, "name")""",
                """MqTextField(v, {}, label = "Email") // ui-string-ok: preview""",
                "// Text(\"in a comment\")",
                "/** Text(\"in a doc\") */",
                "// ui-string-ok: preview\nText(\"Hello\")",
                """val code = "refused"""",
                """Text(joinToString("\n"))""",
                """const val PHYSICAL = "physical_store"""",
                """val tag = Modifier.testTag("Store chip here")""",
                """DateTimeFormatter.ofPattern("d MMM yyyy", Locale.ENGLISH)""",
                """@Preview(name = "Sign-in error, dark", widthDp = 375)""",
                """val sku = Product(1, "SKU1", 3)""",
                """Text("${'$'}{a}, ${'$'}{b}")"""
            )
        for (snippet in fine) {
            assertEquals("wrongly caught: $snippet", emptyList<UiStringScan.Hit>(), UiStringScan.violations(snippet))
        }
        assertEquals(
            emptyList<UiStringScan.Hit>(),
            UiStringScan.violations("// ui-string-ok-file: preview\nText(\"Hello\")")
        )
        // From the marker down nothing is scanned; above it, everything is.
        val split = "Text(\"Real\")\n// ui-string-ok-from: previews below\nText(\"Sample\")"
        assertEquals(listOf(1), UiStringScan.violations(split).map { it.line })
    }
}

/** A small lexer-aware scan: comments are blanked, strings are skipped whole, arguments are read to their comma. */
object UiStringScan {
    data class Hit(val line: Int, val text: String)

    private val namedArguments =
        Regex(
            """\b(?:text|label|contentDescription|supportingText|placeholder|title|errorText|message|actionLabel|""" +
                """headline|supporting|stateDescription|description)\s*=\s*"""
        )
    private val firstArgument = Regex("""\b(?:Text|Mq[A-Z]\w*)\(\s*""")
    private val formatted = Regex("""\bformat\(\s*(?=")""")

    /** A literal that reads as text wherever it sits: a phrase (letters, a space, letters) or a capitalized word. */
    private val phrase = Regex("""[A-Za-z]+\s+[A-Za-z]""")
    private val capitalizedWord = Regex("""^\s*[A-Z][a-z]{2,}""")

    /** Lines whose literals are not text: test tags, date and regex patterns, preview names. */
    private val notText = listOf("testTag(", "ofPattern(", "Regex(", "@Preview")

    /** Money or a count shown by hand: `"$" + price`, `"$${x}"`, `if (n == 1) "item"`. */
    private val rawMoney = Regex("\"\\$(\\$\\{|\\$[A-Za-z]|\"\\s*\\+)")
    private val handRolledPlural = Regex("[=!]=\\s*1\\)?\\s*\\{?\\s*\"")

    fun rawMoneyOrPluralSuffix(line: String): Boolean =
        !line.trimStart().startsWith("//") && (rawMoney.containsMatchIn(line) || handRolledPlural.containsMatchIn(line))

    fun violations(source: String): List<Hit> {
        if (FILE_MARK in source) return emptyList()
        // Previews sit at the end of a file; from the marker down, the sample copy in them is not scanned.
        val cut = source.indexOf(FROM_MARK).let { if (it < 0) source.length else it }
        val code = blankComments(source).substring(0, cut)
        val lines = source.lines()
        val hits = sortedMapOf<Int, Hit>()
        for (pattern in listOf(namedArguments, firstArgument, formatted)) {
            for (match in pattern.findAll(code)) {
                val start = match.range.last + 1
                val literal = literalAtTopLevel(code, start) ?: continue
                val line = code.substring(0, literal).count { it == '\n' } + 1
                val above = lines.getOrNull(line - 2)?.trim().orEmpty()
                if (LINE_MARK in lines[line - 1] || above.startsWith(LINE_MARK)) continue
                hits[line] = Hit(line, lines[line - 1].trim())
            }
        }
        // Second rule: any other literal that reads as text, wherever it sits.
        var i = 0
        while (i < code.length) {
            if (code[i] != '"') {
                i++
                continue
            }
            val end = endOfString(code, i)
            val own = ownText(code.substring(i, end))
            val line = code.substring(0, i).count { it == '\n' } + 1
            val text = lines[line - 1]
            val above = lines.getOrNull(line - 2)?.trim().orEmpty()
            val readsAsText = phrase.containsMatchIn(own) || capitalizedWord.containsMatchIn(own)
            if (readsAsText && notText.none { it in text } && LINE_MARK !in text && !above.startsWith(LINE_MARK)) {
                hits.putIfAbsent(line, Hit(line, text.trim()))
            }
            i = end
        }
        return hits.values.toList()
    }

    /** Index of the first string literal with a letter in the argument starting at [from], outside nested calls. */
    private fun literalAtTopLevel(code: String, from: Int): Int? {
        var depth = 0
        var i = from
        if (i < code.length && code[i] == '{') return null // a lambda: its own calls are scanned on their own
        while (i < code.length) {
            val c = code[i]
            when {
                c == '"' -> {
                    val end = endOfString(code, i)
                    if (depth == 0 && hasLetter(code.substring(i, end))) return i
                    i = end
                    continue
                }
                c == '(' || c == '[' || c == '{' -> depth++
                c == ')' || c == ']' || c == '}' -> if (depth == 0) return null else depth--
                c == ',' && depth == 0 -> return null
            }
            i++
        }
        return null
    }

    /** True when the literal's own words (not its `${}` parts, not escapes) hold a letter. */
    private fun hasLetter(literal: String): Boolean = ownText(literal).any { it.isLetter() }

    /** The literal's own words: no quotes, no `${}` or `$name` parts, no escapes. */
    private fun ownText(literal: String): String {
        val body = literal.trim('"')
        val words = StringBuilder()
        var i = 0
        while (i < body.length) {
            when {
                body.startsWith("\${", i) -> i = endOfBraces(body, i + 1)
                body[i] == '$' && i + 1 < body.length && body[i + 1].isLetter() -> {
                    i++
                    while (i < body.length && (body[i].isLetterOrDigit() || body[i] == '_')) i++
                }
                body[i] == '\\' -> i += if (body.getOrNull(i + 1) == 'u') 6 else 2
                else -> words.append(body[i++])
            }
        }
        return words.toString()
    }

    private fun endOfBraces(text: String, open: Int): Int {
        var depth = 0
        var i = open
        while (i < text.length) {
            if (text[i] == '{') depth++
            if (text[i] == '}' && --depth == 0) return i + 1
            i++
        }
        return text.length
    }

    /** One past the closing quote of the string starting at [start], counting `${}` and raw strings. */
    private fun endOfString(code: String, start: Int): Int {
        if (code.startsWith("\"\"\"", start)) {
            val close = code.indexOf("\"\"\"", start + 3)
            return if (close < 0) code.length else close + 3
        }
        var i = start + 1
        while (i < code.length) {
            when {
                code[i] == '\\' -> i++
                code.startsWith("\${", i) -> i = endOfBraces(code, i + 1) - 1
                code[i] == '"' -> return i + 1
            }
            i++
        }
        return code.length
    }

    /** The source with comments replaced by spaces, so offsets and line numbers stay put. */
    private fun blankComments(source: String): String {
        val out = StringBuilder(source)
        var i = 0
        while (i < source.length) {
            when {
                source[i] == '"' -> i = endOfString(source, i)
                source.startsWith("//", i) -> {
                    val end = source.indexOf('\n', i).let { if (it < 0) source.length else it }
                    for (k in i until end) out[k] = ' '
                    i = end
                }
                source.startsWith("/*", i) -> {
                    val end = source.indexOf("*/", i).let { if (it < 0) source.length else it + 2 }
                    for (k in i until end) if (out[k] != '\n') out[k] = ' '
                    i = end
                }
                else -> i++
            }
        }
        return out.toString()
    }

    private const val LINE_MARK = "// ui-string-ok:"
    private const val FILE_MARK = "// ui-string-ok-file:"
    private const val FROM_MARK = "// ui-string-ok-from:"
}
