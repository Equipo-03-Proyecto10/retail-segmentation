package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.time.Clock
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.util.concurrent.CopyOnWriteArrayList
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.CaptureIncident
import online.maxthecoder.mosaiq.floor.net.CaptureIncidentPage
import online.maxthecoder.mosaiq.floor.net.IncidentReview
import online.maxthecoder.mosaiq.floor.net.json
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.review.ACKNOWLEDGED
import online.maxthecoder.mosaiq.floor.ui.review.OPEN
import online.maxthecoder.mosaiq.floor.ui.review.RESOLVED
import online.maxthecoder.mosaiq.floor.ui.review.ReviewViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * F27-14 (#868): the supervisor's incidents are listed by state and moved forward, every refusal is shown, a lost
 * answer is settled by reading again, the promotions shown are counted by store without a customer, and every
 * read is bounded. Answered by a stateful fake of engagement built from the contract's own examples.
 */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class ReviewViewModelTest {
    private val server = MockWebServer()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private val clock = Clock.fixed(Instant.parse("2026-11-21T15:00:00Z"), ZoneOffset.UTC)
    private val requests = CopyOnWriteArrayList<RecordedRequest>()

    /** The incidents the fake holds, newest first; a review moves one forward. */
    private val held =
        json.decodeFromString(
            CaptureIncidentPage.serializer(),
            contract("engagement/examples/capture-incidents-page.json")
        )
            .captureIncidents.toMutableList()
    private var extraIncidents = 0
    private var reviewAnswer: ((String, String) -> MockResponse?)? = null
    private var eventsAnswer: (Int) -> MockResponse = { ok(200, exposures(listOf(3 to 12), total = 1)) }
    private var totalsAnswer: () -> MockResponse = {
        ok(200, contract("sales/examples/list-sale-promotion-totals.json"))
    }

    private val open get() = held.first { it.status == OPEN }.incidentId

    @Before
    fun start() {
        server.dispatcher =
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    requests += request
                    val url = request.requestUrl!!
                    val path = url.encodedPath
                    return when {
                        path == "/api/engagement/v1/capture-incidents" ->
                            incidentsPage(url.queryParameter("page")!!.toInt())
                        path.startsWith("/api/engagement/v1/capture-incidents/") && path.endsWith("/reviews") ->
                            review(path.substringAfter("capture-incidents/").substringBefore("/"), request)
                        path.startsWith("/api/engagement/v1/capture-incidents/") ->
                            held.firstOrNull { path.endsWith(it.incidentId) }?.let { ok(200, encode(it)) }
                                ?: error(404, "incident_not_found")
                        path == "/api/engagement/v2/engagement-events" ->
                            eventsAnswer(url.queryParameter("page")!!.toInt())
                        path == "/api/sales/v1/sale-promotion-totals" -> totalsAnswer()
                        path == "/api/campaigns/v1/promotions" -> ok(200, PROMOTIONS)
                        path == "/api/stores/v1/stores" -> ok(200, STORES)
                        else -> MockResponse().setResponseCode(404)
                    }
                }
            }
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString(), 1)
        graph.tokens.write(Tokens("access", "refresh"))
        graph.session.signedIn(Profile("S", "Supervisor", "COMMERCIAL_SUPERVISOR"))
    }

    @After
    fun stop() {
        server.shutdown()
        app.database.close()
    }

    private fun encode(incident: CaptureIncident) = json.encodeToString(CaptureIncident.serializer(), incident)

    private fun incidentsPage(page: Int): MockResponse {
        val everything = held + List(extraIncidents) { held.first().copy(incidentId = "extra-$it") }
        val slice = everything.drop((page - 1) * 100).take(100)
        return ok(
            200,
            """{"capture_incidents":[${slice.joinToString(",") { encode(it) }}],""" +
                """"page_info":{"page":$page,"size":100,"total":${everything.size}}}"""
        )
    }

    /** The fake service: forward only, `OPEN` straight to `RESOLVED` allowed, 409 `invalid_transition` otherwise. */
    private fun review(id: String, request: RecordedRequest): MockResponse {
        val body = request.body.clone().readUtf8()
        val status = Regex(""""status":"([A-Z]+)"""").find(body)!!.groupValues[1]
        reviewAnswer?.invoke(id, status)?.let { return it }
        val index = held.indexOfFirst { it.incidentId == id }
        if (index < 0) return error(404, "incident_not_found")
        val rank = listOf(OPEN, ACKNOWLEDGED, RESOLVED)
        if (rank.indexOf(status) <= rank.indexOf(held[index].status)) {
            return ok(409, contract("engagement/examples/invalid-transition.json"))
        }
        val note = Regex(""""note":"([^"]*)"""").find(body)?.groupValues?.get(1)
        held[index] =
            held[index].copy(
                status = status,
                history = held[index].history + IncidentReview(status, note, "2026-11-21T15:00:00Z")
            )
        return ok(201, encode(held[index]))
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private fun settle(done: () -> Boolean) {
        repeat(400) {
            idle()
            if (done()) return
            Thread.sleep(10)
        }
        error("Operation never finished")
    }

    private fun loaded(): ReviewViewModel = ReviewViewModel(graph, clock).also {
        it.load()
        settle { !it.incidents.value.loading && !it.promotions.value.loading && !it.applied.value.loading }
    }

    private fun requestsTo(path: String) = requests.filter { it.requestUrl!!.encodedPath == path }

    private fun reviews() = requests.filter { it.path!!.endsWith("/reviews") }

    @Test
    fun incidentsAreListedByStateWithTheirReviewsAndNobodyIsNamed() {
        val model = loaded()
        val state = model.incidents.value

        assertEquals(listOf(OPEN), state.withStatus(OPEN).map { it.status }.distinct())
        assertEquals("the resolved one is listed too", 1, state.withStatus(RESOLVED).size)
        assertEquals(2, state.withStatus(RESOLVED).single().history.size)
        assertEquals(
            "Reviewed with the associate; the badge was scanned outside the store.",
            state.withStatus(RESOLVED).single().history.last().note
        )
        val list = requestsTo("/api/engagement/v1/capture-incidents").first().requestUrl!!
        assertNull("every state, not only the open ones", list.queryParameter("status"))
        assertEquals("100", list.queryParameter("size"))
        // The phone keeps no person: neither who reported nor who reviewed.
        assertFalse(state.toString().contains("5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33"))
        assertFalse(state.toString().contains("9d4e7a10-52c3-4b8f-a1e6-0c3b5d7f9a21"))
    }

    @Test
    fun anOpenIncidentIsAcknowledgedAndThenResolvedWithANote() {
        val model = loaded()
        val incident = model.incidents.value.withStatus(OPEN).single()

        model.review(incident, ACKNOWLEDGED, "  Checking stock with the warehouse.  ")
        settle { model.incidents.value.busy.isEmpty() }
        val acknowledged = model.incidents.value.incidents.first { it.incidentId == incident.incidentId }
        assertEquals(ACKNOWLEDGED, acknowledged.status)
        assertEquals(
            """{"status":"ACKNOWLEDGED","note":"Checking stock with the warehouse."}""",
            reviews().last().body.clone().readUtf8()
        )
        assertEquals("POST", reviews().last().method)

        model.review(acknowledged, RESOLVED)
        settle { model.incidents.value.busy.isEmpty() }
        val resolved = model.incidents.value.incidents.first { it.incidentId == incident.incidentId }
        assertEquals(RESOLVED, resolved.status)
        assertEquals("no note is no field", """{"status":"RESOLVED"}""", reviews().last().body.clone().readUtf8())
        assertEquals(2, model.incidents.value.withStatus(RESOLVED).size)
        assertTrue(model.incidents.value.reviewErrors.isEmpty())
    }

    @Test
    fun anOpenIncidentCanBeResolvedAtOnceAndAResolvedOneAsksNothing() {
        val model = loaded()
        val incident = model.incidents.value.withStatus(OPEN).single()

        model.review(incident, RESOLVED, "x".repeat(300))
        settle { model.incidents.value.busy.isEmpty() }
        val noteSent = Regex(""""note":"(x+)"""").find(reviews().single().body.clone().readUtf8())!!.groupValues[1]
        assertEquals("a note is cut at the contract's 200", 200, noteSent.length)

        val resolved = model.incidents.value.incidents.first { it.incidentId == incident.incidentId }
        model.review(resolved, ACKNOWLEDGED)
        model.review(resolved, RESOLVED)
        idle()
        assertEquals("a move the contract refuses is never sent", 1, reviews().size)
    }

    @Test
    fun anIncidentSomeoneElseAlreadyMovedIsAnsweredWith409AndShownAsItNowStands() {
        val model = loaded()
        val stale = model.incidents.value.withStatus(OPEN).single()
        // Another supervisor resolved it after this list was read.
        val index = held.indexOfFirst { it.incidentId == stale.incidentId }
        held[index] = held[index].copy(status = RESOLVED)

        model.review(stale, ACKNOWLEDGED)
        settle { model.incidents.value.busy.isEmpty() && model.incidents.value.reviewErrors.isNotEmpty() }
        settle { model.incidents.value.incidents.first { it.incidentId == stale.incidentId }.status == RESOLVED }

        val refusal = model.incidents.value.reviewErrors.getValue(stale.incidentId)
        assertEquals(409, refusal.status)
        assertEquals("invalid_transition", refusal.code)
        assertTrue(
            "the incident was read again",
            requests.any { it.method == "GET" && it.path!!.endsWith(stale.incidentId) }
        )
    }

    @Test
    fun aRefusedReviewIsShownAndTheIncidentIsLeftAsItWas() {
        val model = loaded()
        val incident = model.incidents.value.withStatus(OPEN).single()
        for ((status, code) in listOf(403 to "forbidden_store", 403 to "forbidden_role", 404 to "incident_not_found")) {
            reviewAnswer = { _, _ -> ok(status, contract("engagement/examples/${code.replace('_', '-')}.json")) }
            model.review(incident, ACKNOWLEDGED)
            settle { model.incidents.value.reviewErrors[incident.incidentId]?.code == code }
            assertEquals(status, model.incidents.value.reviewErrors.getValue(incident.incidentId).status)
            assertTrue(incident.incidentId !in model.incidents.value.busy)
            assertEquals(OPEN, model.incidents.value.incidents.first { it.incidentId == incident.incidentId }.status)
        }
    }

    @Test
    fun aRoleTheServiceRefusesSeesThePlainRefusalInsteadOfTheList() {
        server.dispatcher =
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest) =
                    ok(403, contract("engagement/examples/forbidden-role.json"))
            }
        val model = ReviewViewModel(graph, clock)
        model.load()
        settle { !model.incidents.value.loading && !model.promotions.value.loading }

        assertEquals("forbidden_role", model.incidents.value.error?.code)
        assertTrue(model.incidents.value.incidents.isEmpty())
        assertEquals("forbidden_role", model.promotions.value.error?.code)
    }

    @Test
    fun aLostAnswerIsSettledByReadingAgainAndTheReviewIsNeverResentByTheApp() {
        val model = loaded()
        val incident = model.incidents.value.withStatus(OPEN).single()
        // The service commits the resolution, then the connection is cut before any answer.
        reviewAnswer = { id, status ->
            val index = held.indexOfFirst { it.incidentId == id }
            held[index] = held[index].copy(status = status)
            MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE)
        }

        model.review(incident, RESOLVED, "Fixed at the till.")
        settle { model.incidents.value.reviewErrors.containsKey(incident.incidentId) }
        assertEquals("unreachable", model.incidents.value.reviewErrors.getValue(incident.incidentId).code)
        assertEquals("one request, no automatic resend", 1, reviews().size)
        assertTrue(model.incidents.value.busy.isEmpty())

        // The process dies here. A new screen reads the service's truth, not a guess.
        reviewAnswer = null
        val restarted = loaded()
        assertEquals(
            RESOLVED,
            restarted.incidents.value.incidents.first { it.incidentId == incident.incidentId }.status
        )
        assertTrue(restarted.incidents.value.reviewErrors.isEmpty())
        assertEquals("reading again sent no second review", 1, reviews().size)

        // A tap on the stale list is answered 409 and the incident is read again.
        model.review(incident, RESOLVED)
        settle { model.incidents.value.reviewErrors[incident.incidentId]?.code == "invalid_transition" }
    }

    @Test
    fun theListIsBoundedToThreePagesAndSaysHowManyThereAre() {
        extraIncidents = 448
        val model = loaded()

        assertEquals(450, model.incidents.value.total)
        assertEquals(300, model.incidents.value.incidents.size)
        assertEquals(3, requestsTo("/api/engagement/v1/capture-incidents").size)
    }

    @Test
    fun promotionsShownAreCountedByStoreAndCampaignAndNoCustomerIsKept() {
        val customer = "3f6c1d52-8e4a-4b7d-a0c9-5d2e7b1f4a68"
        eventsAnswer = {
            ok(200, exposures(listOf(3 to 12, 3 to 12, 3 to 14, 5 to 12), total = 4, customer = customer))
        }
        val model = loaded()
        val state = model.promotions.value

        assertEquals(
            listOf(
                Triple(3, 12, 2),
                Triple(3, 14, 1),
                Triple(5, 12, 1)
            ),
            state.rows.map { Triple(it.storeId, it.campaignId, it.times) }
        )
        assertEquals("Loyal customers", state.rows.first().name)
        assertNull("a campaign no longer active keeps its number", state.rows[1].name)
        assertEquals(mapOf(3 to "Coyoacan", 5 to "Centro"), state.storeNames)
        assertEquals(LocalDate.parse("2026-11-15"), state.since)
        assertEquals(4, state.counted)
        assertEquals(4, state.total)
        assertFalse("no customer reaches the phone's state", state.toString().contains(customer))
        val url = requestsTo("/api/engagement/v2/engagement-events").first().requestUrl!!
        assertEquals("CAMPAIGN_EXPOSURE", url.queryParameter("type"))
        assertEquals("2026-11-15T00:00:00Z", url.queryParameter("from"))
        assertEquals("false", url.queryParameter("voided"))
        assertEquals("100", url.queryParameter("size"))
        assertNull("no customer is asked for", url.queryParameter("customer_id"))
    }

    @Test
    fun theExposureCountReadsAtMostFivePagesAndSaysHowManyItCounted() {
        eventsAnswer = { page -> ok(200, exposures(List(100) { 3 to 12 }, total = 1203, page = page)) }
        val model = loaded()

        assertEquals(5, requestsTo("/api/engagement/v2/engagement-events").size)
        assertEquals(500, model.promotions.value.counted)
        assertEquals(1203, model.promotions.value.total)
        assertEquals(500, model.promotions.value.rows.single().times)
    }

    @Test
    fun noPromotionShownIsAnEmptyListNotAnError() {
        eventsAnswer = { ok(200, exposures(emptyList(), total = 0)) }
        val model = loaded()
        assertTrue(model.promotions.value.rows.isEmpty())
        assertNull(model.promotions.value.error)
    }

    // F27-16 (#1143): the promotions applied in sales, as sales-service totals them.

    @Test
    fun appliedPromotionsComeTotalledFromSalesInOneRequestOverTheSameSevenDays() {
        val model = loaded()
        val state = model.applied.value

        // The contract's own example: store 3 has promotions 12 and 15, store 7 has 12.
        assertEquals(
            listOf(Triple(3, 12, 4), Triple(3, 15, 2), Triple(7, 12, 1)),
            state.rows.map { Triple(it.storeId, it.promotionId, it.lines) }
        )
        assertEquals(listOf(9, 3, 2), state.rows.map { it.units })
        assertEquals(listOf("22.50", "12.00", "5.00"), state.rows.map { it.discount })
        assertEquals(LocalDate.parse("2026-11-15"), state.since)
        assertEquals(mapOf(3 to "Coyoacan", 5 to "Centro"), state.storeNames)
        assertNull(state.error)
        val asked = requestsTo("/api/sales/v1/sale-promotion-totals")
        assertEquals("the phone counts nothing: one request", 1, asked.size)
        val url = asked.single().requestUrl!!
        assertEquals("2026-11-15T00:00:00Z", url.queryParameter("from"))
        assertEquals("today is in the window", "2026-11-22T00:00:00Z", url.queryParameter("to"))
        assertNull("the service narrows to the caller's stores", url.queryParameter("store_id"))
    }

    @Test
    fun anAppliedPromotionIsNamedWhenItIsActiveAndKeepsItsNumberOtherwise() {
        totalsAnswer = {
            ok(
                200,
                """{"from":"2026-11-15T00:00:00Z","to":"2026-11-22T00:00:00Z","totals":[""" +
                    """{"store_id":3,"promotion_id":38,"lines":2,"units":3,"discount":"7.50"},""" +
                    """{"store_id":3,"promotion_id":41,"lines":1,"units":1,"discount":"2.00"}]}"""
            )
        }
        val rows = loaded().applied.value.rows

        assertEquals("Loyal customers", rows.first { it.promotionId == 38 }.name)
        assertNull(rows.first { it.promotionId == 41 }.name)
    }

    @Test
    fun noPromotionAppliedIsAnEmptyListNotAnError() {
        totalsAnswer = { ok(200, contract("sales/examples/list-sale-promotion-totals-empty.json")) }
        val state = loaded().applied.value
        assertTrue(state.rows.isEmpty())
        assertNull(state.error)
    }

    @Test
    fun aRefusedTotalIsShownAsTheRefusalAndTheOtherPartsStillLoad() {
        totalsAnswer = { ok(403, contract("sales/examples/forbidden-role.json")) }
        val model = loaded()

        assertEquals("forbidden_role", model.applied.value.error?.code)
        assertTrue(model.applied.value.rows.isEmpty())
        assertNull(model.promotions.value.error)
        assertTrue(model.incidents.value.incidents.isNotEmpty())
    }

    private fun assertTotalsRefusedAs(code: String, answer: () -> MockResponse) {
        totalsAnswer = answer
        val model = loaded()
        assertEquals(code, model.applied.value.error?.code)
        assertTrue("no totals after $code", model.applied.value.rows.isEmpty())
        assertFalse(model.applied.value.loading)
        assertNull("the other parts are not blocked by $code", model.promotions.value.error)
        assertTrue(model.incidents.value.incidents.isNotEmpty())
    }

    @Test
    fun aStoreThatIsNotTheCallersIsRefusedWithoutTotals() =
        assertTotalsRefusedAs("forbidden_store") { ok(403, contract("sales/examples/forbidden-store.json")) }

    @Test
    fun aWindowTheServiceRefusesIsShownAsInvalidQuery() = assertTotalsRefusedAs("invalid_query") {
        ok(422, contract("sales/examples/invalid-promotion-totals-window.json"))
    }

    @Test
    fun aSalesDatabaseThatIsDownIsShownAsTheServiceUnavailable() = assertTotalsRefusedAs("database_unavailable") {
        ok(503, contract("sales/examples/database-unavailable.json"))
    }

    @Test
    fun aPhoneThatCannotReachTheServiceShowsUnreachable() =
        assertTotalsRefusedAs("unreachable") { MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE) }

    @Test
    fun retryingARefusedTotalReadsAgainAndShowsTheTotals() {
        totalsAnswer = { ok(503, contract("sales/examples/database-unavailable.json")) }
        val model = loaded()
        assertEquals("database_unavailable", model.applied.value.error?.code)

        totalsAnswer = { ok(200, contract("sales/examples/list-sale-promotion-totals.json")) }
        model.loadApplied()
        settle { !model.applied.value.loading }

        assertNull(model.applied.value.error)
        assertEquals(3, model.applied.value.rows.size)
        assertEquals("one request per read", 2, requestsTo("/api/sales/v1/sale-promotion-totals").size)
    }

    @Test
    fun anotherUsersIncidentsNeverStayOnScreen() {
        val model = loaded()
        assertNotNull(model.incidents.value.incidents.firstOrNull())
        graph.session.signedOut()
        idle()
        assertTrue(model.incidents.value.incidents.isEmpty())
        assertTrue(model.promotions.value.rows.isEmpty())
        assertTrue(model.applied.value.rows.isEmpty())
    }

    private fun exposures(
        events: List<Pair<Int, Int>>,
        total: Int,
        customer: String = "3f6c1d52-8e4a-4b7d-a0c9-5d2e7b1f4a68",
        page: Int = 1
    ): String {
        val items =
            events.mapIndexed { i, (store, campaign) ->
                """{"event_id":"e$page-$i","client_event_id":"c$page-$i","type":"CAMPAIGN_EXPOSURE",""" +
                    """"store_id":$store,"product_id":null,"customer_id":"$customer","campaign_id":$campaign,""" +
                    """"voided":false,"history":[]}"""
            }
        return """{"engagement_events":[${items.joinToString(
            ","
        )}],"page_info":{"page":$page,"size":100,"total":$total}}"""
    }

    private companion object {
        val PROMOTIONS =
            """{"promotions":[{"promotion_id":38,"campaign_id":12,"name":"Loyal customers",""" +
                """"discount_percent":"15.00","starts_on":"2026-11-01","ends_on":"2026-11-30",""" +
                """"store_ids":[3],"channel_ids":[3],"is_active":true}],""" +
                """"page_info":{"page":1,"size":50,"total":1}}"""
        val STORES =
            """{"stores":[{"store_id":3,"name":"Coyoacan","city":"CDMX","state":"CDMX","is_active":true},""" +
                """{"store_id":5,"name":"Centro","city":"CDMX","state":"CDMX","is_active":true}],""" +
                """"page_info":{"page":1,"size":100,"total":2}}"""
    }
}
