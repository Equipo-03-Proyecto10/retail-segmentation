package online.maxthecoder.mosaiq.floor

import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element

/** UX-12: words live in `res/values/strings.xml`, counts are plurals, and English is the only locale. */
class StringResourcesTest {
    private val res = File("src/main/res")
    private val strings = File(res, "values/strings.xml")

    private fun elements(tag: String): List<Element> {
        val document = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(strings)
        val nodes = document.getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }

    @Test
    fun englishIsTheOnlyLocale() {
        val translated =
            res.listFiles { f -> f.isDirectory && f.name.startsWith("values-") }.orEmpty()
                .filter { dir -> dir.listFiles().orEmpty().any { it.name.startsWith("strings") } }
        assertEquals("no translated strings", emptyList<String>(), translated.map { it.name })
    }

    @Test
    fun everyPluralsResourceHasOneAndOther() {
        val plurals = elements("plurals")
        assertTrue("the app has plurals", plurals.size >= 5)
        plurals.forEach { resource ->
            val items = resource.getElementsByTagName("item")
            val quantities = (0 until items.length).map { (items.item(it) as Element).getAttribute("quantity") }
            assertTrue(
                "${resource.getAttribute("name")} has one and other: $quantities",
                "one" in quantities && "other" in quantities
            )
        }
    }

    @Test
    fun noWordIsPluralisedByHandInAResource() {
        val handRolled = Regex("""\b\w+\(s\)""")
        val found =
            (elements("string") + elements("item")).filter { handRolled.containsMatchIn(it.textContent) }
                .map { it.textContent }
        assertEquals("write counts as plurals, not word(s)", emptyList<String>(), found)
    }
}
