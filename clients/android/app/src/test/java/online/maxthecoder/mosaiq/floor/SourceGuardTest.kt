package online.maxthecoder.mosaiq.floor

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The rules a reviewer would otherwise check by eye (ADR-0041). */
class SourceGuardTest {
    private val main = File("src/main/java")
    private val sources = main.walkTopDown().filter { it.isFile && it.extension == "kt" }.toList()
    private val registry = File("../../../contracts/registry.yaml")

    @Test
    fun theAppNeverTouchesXml() {
        val forbidden = Regex("application/xml|XmlPullParser|SimpleXml")
        val hits = sources.filter { forbidden.containsMatchIn(it.readText()) }
        assertEquals("ADR-0041: Android is JSON only", emptyList<File>(), hits)
    }

    @Test
    fun nothingCanWriteATokenToLogcat() {
        for (file in sources) {
            val text = file.readText()
            assertTrue("${file.name} uses a logging interceptor", "HttpLoggingInterceptor" !in text)
            val logged = Regex("""Log\.[a-z]\([^)]*(?i:token)""").containsMatchIn(text)
            assertTrue("${file.name} logs a token", !logged)
        }
    }

    @Test
    fun everyPathIsAResourceOfTheServiceItCalls() {
        assertTrue("the registry is at ${registry.path}", registry.isFile)
        val owners = mutableMapOf<String, Set<String>>()
        var service: String? = null
        for (line in registry.readLines()) {
            Regex("""^\s*- name: ([a-z-]+)""").find(line)?.let { service = it.groupValues[1] }
            Regex("""^\s*resources: \[(.*)]""").find(line)?.let { match ->
                owners[service!!] = match.groupValues[1].split(",").map { it.trim() }.toSet()
            }
        }
        val calls = Regex("""Services\.([A-Z_]+) \+ "([a-z0-9.-]+)""")
        val services = File(main, "online/maxthecoder/mosaiq/floor/net/Services.kt").readText()
        val prefixes =
            Regex("""const val ([A-Z_]+) = "api/([a-z-]+)/v1/"""")
                .findAll(services)
                .associate { it.groupValues[1] to it.groupValues[2] }
        val found = sources.flatMap { calls.findAll(it.readText()).toList() }
        assertTrue("some API calls exist", found.isNotEmpty())
        for (call in found) {
            val name = prefixes.getValue(call.groupValues[1])
            val resource = call.groupValues[2]
            assertTrue("$resource is not a resource of $name", resource in owners.getValue(name))
        }
    }

    /** ADR-0041: a retry id is generated once, at the tap, never per attempt. */
    @Test
    fun noWorkerGeneratesAClientId() {
        val workers = sources.filter { it.name.contains("Worker") }
        assertTrue("a worker exists", workers.isNotEmpty())
        for (file in workers) {
            assertTrue("${file.name} generates ids", "UUID.randomUUID" !in file.readText())
        }
    }

    /** UX-14: collect view model state with the lifecycle, and assert nothing non-null where a null can arrive. */
    @Test
    fun screensCollectWithTheLifecycleAndAvoidNonNullAssertions() {
        val floor = File(main, "online/maxthecoder/mosaiq/floor")
        for (path in listOf(
            "ui/shell/FloorShell.kt",
            "ui/shell/SignOut.kt",
            "ui/checkin/CheckInScreen.kt",
            "ui/checkin/StorePicker.kt",
            "ui/outbox/OutboxScreen.kt",
            "ui/SalesScreen.kt",
            "ui/CustomerInsights.kt"
        )) {
            assertTrue("$path uses collectAsState", ".collectAsState()" !in File(floor, path).readText())
        }
        for (path in listOf(
            "ui/shell/FloorShell.kt",
            "ui/shell/SignOut.kt",
            "ui/checkin/CheckInScreen.kt",
            "ui/checkin/StorePicker.kt",
            "ui/outbox/OutboxScreen.kt",
            "ui/CustomerInsights.kt",
            "net/TokenAuthenticator.kt",
            "outbox/OutboxWorker.kt"
        )) {
            assertTrue("$path uses !!", "!!" !in File(floor, path).readText())
        }
    }
}
