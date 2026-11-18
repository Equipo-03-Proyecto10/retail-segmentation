package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.CardRead
import online.maxthecoder.mosaiq.floor.ui.IdentifyViewModel
import online.maxthecoder.mosaiq.floor.ui.isLoyaltyCode
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

private fun customersExample(name: String): String = File("../../../contracts/customers/examples", name).readText()

/** customers-service's floor reads, answering with the contract's examples. */
private class FloorCustomers : Dispatcher() {
    var lookup: MockResponse = ok(200, customersExample("lookup-loyalty-card.json"))
    var view = "get-floor-customer-sales-associate.json"
    val requests = CopyOnWriteArrayList<RecordedRequest>()

    override fun dispatch(request: RecordedRequest): MockResponse {
        requests += request
        val path = request.requestUrl!!.encodedPath
        return when {
            path == "/api/customers/v1/loyalty-cards/lookup" -> lookup
            path == "/api/customers/v1/customers/floor-search" ->
                ok(200, customersExample("search-floor-customers.json").replace("\"total\": 1", "\"total\": 41"))
            path.endsWith("/floor-view") -> ok(200, customersExample(view))
            else -> error(404, "not_found")
        }
    }
}

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class IdentifyViewModelTest {
    private val server = MockWebServer()
    private val services = FloorCustomers()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private val code = "7M3QX9T2KD5VHA8R0WBN4ZPCJE"
    private val customer = "7b3ad781-c5de-4f40-ae50-1fa825ce2b25"

    @Before
    fun start() {
        server.dispatcher = services
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString())
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn(Profile("A", "Demo A", "SALES_ASSOCIATE"))
    }

    @After
    fun stop() {
        server.shutdown()
        app.database.close()
    }

    private fun settle(done: () -> Boolean) {
        repeat(300) {
            shadowOf(Looper.getMainLooper()).idle()
            if (done()) return
            Thread.sleep(10)
        }
        error("never settled")
    }

    @Test
    fun theContractsCardCodeIsTheOnlyThingSentToLookup() {
        assertTrue(isLoyaltyCode(code))
        listOf("", "42", code.lowercase(), code + "0", code.replace('7', 'I'), "https://example.com/$code").forEach {
            assertFalse(it, isLoyaltyCode(it))
        }
    }

    @Test
    fun aValidCardOpensTheProfileAndTheCodeIsNeverKept() {
        val model = IdentifyViewModel(graph)
        val opened = mutableListOf<String>()
        model.onDecoded(code) { opened += it }
        settle { model.profile.value.customer != null }

        assertEquals(listOf(customer), opened)
        val profile = model.profile.value
        assertTrue(profile.byCard)
        assertEquals("Jordan Lee", profile.customer?.name)
        // A seller's floor view has no contact elements, and the app fills none in.
        assertNull(profile.customer?.email)
        assertNull(profile.customer?.phone)

        val lookup = services.requests.first()
        assertEquals("POST", lookup.method)
        assertEquals("""{"code":"$code"}""", lookup.body.readUtf8())
        assertFalse("the code is never in a URL", services.requests.any { code in it.requestUrl.toString() })
        assertEquals("/api/customers/v1/customers/$customer/floor-view", services.requests[1].requestUrl!!.encodedPath)

        val state = listOf(model.card.value, model.search.value, model.profile.value).joinToString()
        assertFalse("the code is not in the screen's state", code in state)
        val written = app.dataDir.walkTopDown().filter { it.isFile }.filter { code in it.readText(Charsets.ISO_8859_1) }
        assertEquals("the code is written nowhere on the device", emptyList<File>(), written.toList())
    }

    @Test
    fun anUnknownOrRevokedCardSaysSoAndOpensNothing() {
        val answers =
            mapOf(
                ok(404, customersExample("card-not-found.json")) to CardRead.Unknown,
                ok(409, customersExample("card-revoked.json")) to CardRead.Revoked
            )
        answers.forEach { (answer, expected) ->
            services.lookup = answer
            val model = IdentifyViewModel(graph)
            val opened = mutableListOf<String>()
            model.onDecoded(code) { opened += it }
            settle { model.card.value != CardRead.Looking }

            assertEquals(expected, model.card.value)
            assertEquals(emptyList<String>(), opened)
            assertNull(model.profile.value.customerId)
        }
        assertTrue(services.requests.none { it.requestUrl!!.encodedPath.endsWith("/floor-view") })
    }

    @Test
    fun aQrThatIsNotALoyaltyCodeIsNeverSent() {
        val model = IdentifyViewModel(graph)
        model.onDecoded("42") { error("nothing opens") }

        assertEquals(CardRead.NotACard, model.card.value)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun theSearchKeepsTheContractsMinimumQueryAndPageSize() {
        val model = IdentifyViewModel(graph)
        model.search("  Jo  ")
        assertEquals("under three characters is never sent", 0, server.requestCount)

        model.search(" Jordan ")
        settle { !model.search.value.loading }
        val first = services.requests.single().requestUrl!!
        assertEquals("Jordan", first.queryParameter("q"))
        assertEquals("1", first.queryParameter("page"))
        assertEquals("20", first.queryParameter("size"))
        assertEquals(3, model.search.value.totalPages)
        assertEquals("Jordan Lee", model.search.value.customers.single().name)

        model.search(page = 2)
        settle { !model.search.value.loading }
        assertEquals("2", services.requests.last().requestUrl!!.queryParameter("page"))
        assertEquals("Jordan", services.requests.last().requestUrl!!.queryParameter("q"))
    }

    @Test
    fun aNewUserStartsWithNothingTheLastOneFound() {
        val model = IdentifyViewModel(graph)
        model.search("Jordan")
        settle { !model.search.value.loading }
        model.open(customer)
        settle { model.profile.value.customer != null }

        graph.session.signedIn(Profile("B", "Demo B", "LOYALTY_OFFICER"))
        settle { model.profile.value.customerId == null }
        assertTrue(model.search.value.customers.isEmpty())
    }
}
