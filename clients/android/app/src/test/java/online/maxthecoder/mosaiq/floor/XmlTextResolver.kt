package online.maxthecoder.mosaiq.floor

import java.io.File
import java.util.Locale
import javax.xml.parsers.DocumentBuilderFactory
import org.w3c.dom.Element

/**
 * Lets plain JVM tests read the words code gets from [AppText]: it parses `values/strings.xml` and maps the
 * generated `R` ids back to names. Found by `ServiceLoader` (META-INF/services), so no test has to set it up.
 */
class XmlTextResolver : AppText.Resolver {
    private val strings = mutableMapOf<String, String>()
    private val plurals = mutableMapOf<String, Map<String, String>>()
    private val stringNames = names("online.maxthecoder.mosaiq.floor.R\$string")
    private val pluralNames = names("online.maxthecoder.mosaiq.floor.R\$plurals")

    init {
        val document = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(
            File("src/main/res/values/strings.xml")
        )
        val root = document.documentElement.childNodes
        for (index in 0 until root.length) {
            val node = root.item(index) as? Element ?: continue
            val name = node.getAttribute("name")
            when (node.tagName) {
                "string" -> strings[name] = unescape(node.textContent)
                "plurals" -> {
                    val items = node.getElementsByTagName("item")
                    plurals[name] =
                        (0 until items.length).associate {
                            val item = items.item(it) as Element
                            item.getAttribute("quantity") to unescape(item.textContent)
                        }
                }
            }
        }
    }

    override fun string(id: Int, args: Array<out Any>): String {
        val name = stringNames.getValue(id)
        return String.format(Locale.ENGLISH, strings.getValue(name), *args)
    }

    override fun plural(id: Int, quantity: Int, args: Array<out Any>): String {
        val forms = plurals.getValue(pluralNames.getValue(id))
        val form = if (quantity == 1) "one" else "other"
        return String.format(Locale.ENGLISH, forms.getValue(form), *args)
    }

    private fun names(type: String): Map<Int, String> =
        Class.forName(type).fields.associate { it.getInt(null) to it.name }

    /** The escapes aapt applies to a resource's text. */
    private fun unescape(text: String): String =
        text.trim().replace(Regex("""\\(.)""")) { it.groupValues[1].replace("n", "\n") }
}
