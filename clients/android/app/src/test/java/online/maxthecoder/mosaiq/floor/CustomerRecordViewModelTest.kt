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
import online.maxthecoder.mosaiq.floor.auth.RecordPart
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.CustomerRecordViewModel
import online.maxthecoder.mosaiq.floor.ui.GRANTED
import online.maxthecoder.mosaiq.floor.ui.WITHDRAWN
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

private fun example(service: String, name: String): String =
    File("../../../contracts/$service/examples", name).readText()

/** customers, customer-preferences, sales and categories, answering with their contracts' examples. */
private class RecordServices : Dispatcher() {
    var register: MockResponse = ok(201, example("customers", "register-customer.json"))
    var consentWrite: MockResponse = ok(201, example("customer-preferences", "record-consent.json"))
    var preferenceWrite: MockResponse? = null
    val requests = CopyOnWriteArrayList<RecordedRequest>()
    val bodies = CopyOnWriteArrayList<String>()

    override fun dispatch(request: RecordedRequest): MockResponse {
        requests += request
        bodies += request.body.readUtf8()
        val path = request.requestUrl!!.encodedPath
        val prefs = "/api/customer-preferences/v1/preference-profiles/"
        return when {
            path == "/api/customers/v1/customers" && request.method == "POST" -> register
            path.endsWith("/floor-view") -> ok(200, example("customers", "get-floor-customer-sales-associate.json"))
            path == "/api/sales/v1/channels" -> ok(200, example("sales", "list-channels.json"))
            path == "/api/categories/v1/categories" -> ok(200, example("categories", "list-categories.json"))
            path.startsWith(prefs) && path.endsWith("/consent-decisions") && request.method == "POST" -> consentWrite
            path.startsWith(prefs) && path.endsWith("/consent-decisions") ->
                ok(200, example("customer-preferences", "list-consent-decisions.json"))
            path.startsWith(prefs) && path.endsWith("/consent") -> ok(
                200,
                example("customer-preferences", "get-consent.json")
            )
            path.startsWith(prefs) && path.endsWith("/preference-changes") ->
                ok(200, example("customer-preferences", "list-preference-changes.json"))
            path.startsWith(prefs) && path.endsWith("/interest-categories") ->
                preferenceWrite ?: ok(200, example("customer-preferences", "replace-interest-categories.json"))
            path.startsWith(prefs) && path.endsWith("/preferred-channels") ->
                preferenceWrite ?: ok(200, example("customer-preferences", "replace-preferred-channels.json"))
            path.startsWith(prefs) -> ok(200, example("customer-preferences", "get-profile.json"))
            else -> error(404, "not_found")
        }
    }

    fun sent(method: String, suffix: String): List<Pair<RecordedRequest, String>> =
        requests.zip(bodies).filter { (r, _) -> r.method == method && r.requestUrl!!.encodedPath.endsWith(suffix) }
}

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class CustomerRecordViewModelTest {
    private val server = MockWebServer()
    private val services = RecordServices()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private val customer = "7b3ad781-c5de-4f40-ae50-1fa825ce2b25"
    private val registered = "6b8c1d78-38d1-45ba-8805-ddaf3a6c62b4"

    @Before
    fun start() {
        server.dispatcher = services
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString())
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn(Profile("A", "Demo A", "LOYALTY_OFFICER"))
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

    private fun enrolled(): CustomerRecordViewModel {
        val model = CustomerRecordViewModel(graph)
        model.startEnrolment()
        model.loadChannels()
        settle { model.enrol.value.channels.isNotEmpty() }
        return model
    }

    @Test
    fun registrationAsksEachPurposeAndNothingDefaultsToYes() {
        val model = enrolled()
        assertTrue("no purpose starts answered", model.enrol.value.consent.isEmpty())
        assertEquals("the registration channel defaults to the store", 3, model.enrol.value.selectedChannelId)

        val opened = mutableListOf<String>()
        model.submit("Ana Torres", "", "", opened::add)
        model.answerConsent("PERSONALIZATION", GRANTED)
        model.answerConsent("MARKETING_CONTACT", WITHDRAWN)
        model.submit("Ana Torres", "", "", opened::add)
        assertEquals("nothing is sent while a purpose is unanswered", 1, server.requestCount)

        model.answerConsent("PROFILING", WITHDRAWN)
        model.submit("Ana Torres", "", "", opened::add)
        settle { opened.isNotEmpty() }

        assertEquals(listOf(registered), opened)
        assertEquals(1, services.sent("POST", "/api/customers/v1/customers").size)
        val (consent, body) = services.sent("POST", "/consent-decisions").single()
        assertEquals(
            "/api/customer-preferences/v1/preference-profiles/$registered/consent-decisions",
            consent.requestUrl!!.encodedPath
        )
        assertEquals(
            """{"decisions":[{"purpose":"PERSONALIZATION","decision":"GRANTED"},""" +
                """{"purpose":"MARKETING_CONTACT","decision":"WITHDRAWN"},""" +
                """{"purpose":"PROFILING","decision":"WITHDRAWN"}]}""",
            body
        )
        assertTrue("the form is empty for the next customer", model.enrol.value.consent.isEmpty())
    }

    @Test
    fun aConsentThatFailsAfterRegistrationIsSentAgainWithoutRegisteringTwice() {
        services.consentWrite = error(503, "database_unavailable")
        val model = enrolled()
        listOf("PERSONALIZATION", "MARKETING_CONTACT", "PROFILING").forEach { model.answerConsent(it, GRANTED) }
        val opened = mutableListOf<String>()
        model.submit("Ana Torres", "ana.torres@example.com", "", opened::add)
        settle { !model.enrol.value.sending }

        assertEquals(registered, model.enrol.value.customer?.customerId)
        assertEquals("database_unavailable", model.enrol.value.error?.code)
        assertTrue(opened.isEmpty())

        services.consentWrite = ok(201, example("customer-preferences", "record-consent.json"))
        model.submit("", "", "", opened::add)
        settle { opened.isNotEmpty() }
        assertEquals(1, services.sent("POST", "/api/customers/v1/customers").size)
        assertEquals(2, services.sent("POST", "/consent-decisions").size)
    }

    private fun opened(): CustomerRecordViewModel {
        val model = CustomerRecordViewModel(graph)
        model.open(customer)
        settle { model.record.value.ready }
        return model
    }

    @Test
    fun theRecordShowsConsentAndPreferencesAndOnlyAChangedPurposeIsSent() {
        val model = opened()
        val state = model.record.value
        assertEquals("Jordan Lee", state.customer?.name)
        assertEquals(setOf(1, 3), state.channelsDraft)
        assertEquals(setOf(2, 11, 12), state.interestsDraft)
        // A programme default is not the customer's word: it is offered for confirmation.
        assertEquals(listOf("MARKETING_CONTACT", "PROFILING"), state.consentChanges.map { it.purpose })

        model.chooseConsent("MARKETING_CONTACT", GRANTED)
        model.chooseConsent("PROFILING", WITHDRAWN)
        model.chooseConsent("PERSONALIZATION", GRANTED)
        model.saveConsent()
        settle { model.record.value.write(RecordPart.CONSENT).saved }

        val body = services.sent("POST", "/consent-decisions").single().second
        assertEquals(
            """{"decisions":[{"purpose":"MARKETING_CONTACT","decision":"GRANTED"},""" +
                """{"purpose":"PROFILING","decision":"WITHDRAWN"}]}""",
            body
        )
        // The history is read again, so it shows what was just recorded.
        settle { model.history.value.open && !model.history.value.loading }
        assertTrue(model.history.value.consent.isNotEmpty())
    }

    @Test
    fun aPreferenceChangeIsSentAsTheWholeSetAndTheHistoryShowsIt() {
        val model = opened()
        model.toggleInterest(11)
        model.saveInterests()
        settle { model.record.value.write(RecordPart.INTERESTS).saved }
        assertEquals("""{"category_ids":[2,12]}""", services.sent("PUT", "/interest-categories").single().second)

        model.toggleChannel(1)
        model.toggleChannel(3)
        model.saveChannels()
        settle { model.record.value.write(RecordPart.PREFERRED_CHANNELS).saved }
        assertEquals("""{"channel_ids":[]}""", services.sent("PUT", "/preferred-channels").single().second)

        settle { model.history.value.open && !model.history.value.loading }
        assertTrue(model.history.value.changes.isNotEmpty())
        assertTrue(services.sent("GET", "/preference-changes").isNotEmpty())
    }

    @Test
    fun aRefusedWriteIsShownAndNothingIsChangedLocally() {
        val model = opened()
        services.preferenceWrite = ok(403, example("customer-preferences", "forbidden-role.json"))
        model.toggleInterest(2)
        model.saveInterests()
        settle { !model.record.value.write(RecordPart.INTERESTS).saving }

        assertEquals("forbidden_role", model.record.value.write(RecordPart.INTERESTS).error?.code)
        assertFalse(model.record.value.write(RecordPart.INTERESTS).saved)
        assertEquals(listOf(2, 11, 12), model.record.value.profile?.interestCategoryIds)
        assertFalse("a refused write reads no history", model.history.value.open)
    }

    @Test
    fun theHistoryIsReadOnlyWhenAsked() {
        val model = opened()
        assertTrue(services.sent("GET", "/consent-decisions").isEmpty())
        model.toggleHistory()
        settle { model.history.value.open && !model.history.value.loading }
        assertEquals(1, services.sent("GET", "/consent-decisions").size)
    }

    @Test
    fun aNewUserStartsWithNothingTheLastOneOpened() {
        val model = opened()
        graph.session.signedIn(Profile("B", "Demo B", "LOYALTY_OFFICER"))
        settle { model.record.value.customerId == null }
        assertNull(model.record.value.consent)
    }
}
