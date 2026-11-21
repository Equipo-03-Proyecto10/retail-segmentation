package online.maxthecoder.mosaiq.floor

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.ListenableWorker
import androidx.work.testing.TestListenableWorkerBuilder
import java.time.Instant
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.alerts.AlertKind
import online.maxthecoder.mosaiq.floor.alerts.CaptureIncidents
import online.maxthecoder.mosaiq.floor.alerts.NotificationPollWorker
import online.maxthecoder.mosaiq.floor.alerts.Notifier
import online.maxthecoder.mosaiq.floor.alerts.PollOutcome
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** ADR-0071's compliance cases for the poll, the capture-error alert, its incident report and sign-out. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class AlertsTest {
    /** What would have been posted, in order. */
    private class Recording : Notifier {
        val posted = mutableListOf<Triple<AlertKind, Int, String?>>()
        var cancelledAll = 0

        override fun post(kind: AlertKind, count: Int, campaignName: String?, subjectId: String?): Boolean {
            posted += Triple(kind, count, campaignName ?: subjectId)
            return true
        }

        override fun cancel(kind: AlertKind, subjectId: String?) = Unit

        override fun cancelAll() {
            cancelledAll++
        }

        override fun enabled() = false
    }

    private val server = MockWebServer()
    private val notifier = Recording()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private var scheduled = 0
    private var cancelled = 0

    /** What each read answers; the dispatcher reads it, so a test changes it between polls. */
    @Volatile private var promotions = page("promotions", emptyList())

    @Volatile private var incidents = page("capture_incidents", emptyList())

    @Volatile private var unauthorized = false

    @Volatile private var unavailable = false

    @Before
    fun start() {
        server.dispatcher =
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val path = request.path.orEmpty()
                    return when {
                        unavailable -> error(503, "auth_unavailable")
                        path.startsWith(REFRESH) -> error(401, "session_expired")
                        unauthorized -> error(401, "invalid_token")
                        path.startsWith("/api/campaigns/v1/promotions") -> ok(200, promotions)
                        path.startsWith("/api/engagement/v1/capture-incidents") && request.method == "GET" ->
                            ok(200, incidents)
                        path.startsWith("/api/engagement/v1/capture-incidents") -> ok(201, "{}")
                        path.startsWith("/api/sales/v1/sales") ->
                            ok(409, SALE_REFUSED)
                                .setHeader(ApiError.CORRELATION_HEADER, "corr-sale-1")
                        else -> error(404, "not_found")
                    }
                }
            }
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph =
            app.build(
                server.url("/").toString(),
                notifier = notifier,
                schedulePoll = { scheduled++ },
                cancelPoll = { cancelled++ }
            )
    }

    @After
    fun stop() {
        server.shutdown()
        app.database.close()
    }

    private fun signIn(role: String, userId: String = "A") {
        graph.tokens.write(Tokens("token-$userId", "refresh-$userId"))
        graph.session.signedIn(Profile(userId, "Demo $userId", role))
    }

    private fun page(name: String, items: List<String>) =
        """{"$name":[${items.joinToString(",")}],"page_info":{"page":1,"size":50,"total":${items.size}}}"""

    private fun promotion(promotionId: Int, campaignId: Int, name: String, endsOn: String = "2099-12-31") =
        """{"promotion_id":$promotionId,"campaign_id":$campaignId,"name":"$name","discount_percent":"15.00",""" +
            """"starts_on":"2026-01-01","ends_on":"$endsOn","store_ids":[3],"channel_ids":[1],"is_active":true}"""

    private fun incident(id: String, recordedAt: String) =
        """{"incident_id":"$id","client_incident_id":"2b8f4d1a-6c37-5e90-b1a2-3d4c5e6f7a81","source_type":"SALE",""" +
            """"source_operation_id":"d4f1b3a2-9c58-4e07-a6b1-0f2e8c3d5a74","source_correlation_id":"corr-1",""" +
            """"store_id":3,"refused_status":409,"refused_code":"stock_unavailable","attempts":1,""" +
            """"occurred_at":"2026-11-05T14:02:10Z","line_count":2,""" +
            """"reported_by":"5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33","recorded_at":"$recordedAt","status":"OPEN",""" +
            """"history":[]}"""

    private fun requests(): List<RecordedRequest> =
        generateSequence { server.takeRequest(100, TimeUnit.MILLISECONDS) }.toList()

    @Test
    fun theFirstPollIsSilentAndTheSecondNotifiesOncePerNewCampaign() = runBlocking {
        signIn("SALES_ASSOCIATE")
        promotions = page("promotions", listOf(promotion(38, 12, "Loyal customers")))
        assertEquals(PollOutcome.DONE, graph.alerts.poll())
        assertEquals("the baseline posts nothing", emptyList<Any>(), notifier.posted)
        assertEquals(1, graph.alerts.inbox.value.campaigns.size)

        // A second promotion of campaign 12 is not a new campaign; campaign 14 is.
        promotions =
            page(
                "promotions",
                listOf(
                    promotion(38, 12, "Loyal customers"),
                    promotion(39, 12, "Loyal customers, extra"),
                    promotion(41, 14, "Weekend basket")
                )
            )
        graph.alerts.poll()
        graph.alerts.poll()

        assertEquals(listOf(Triple(AlertKind.CAMPAIGN_STARTED, 1, "Weekend basket")), notifier.posted)
        val read = requests().first { it.path.orEmpty().startsWith("/api/campaigns") }
        assertEquals("true", read.requestUrl?.queryParameter("active"))
        assertTrue("50 or fewer per request", (read.requestUrl?.queryParameter("size")?.toInt() ?: 0) <= 50)
        assertTrue(
            "a seller never reads incidents",
            requests().none { it.path.orEmpty().contains("capture-incidents") }
        )
    }

    @Test
    fun theChosenStoreNarrowsTheCampaignReadAndAnEndingCampaignIsToldOnce() = runBlocking {
        signIn("STORE_MANAGER")
        graph.session.choose(ChosenStore(3, "Centro"))
        graph.alerts.poll()
        val today = java.time.LocalDate.now().plusDays(1).toString()
        promotions = page("promotions", listOf(promotion(38, 12, "Loyal customers", endsOn = "2099-12-31")))
        graph.alerts.poll()
        promotions = page("promotions", listOf(promotion(38, 12, "Loyal customers", endsOn = today)))
        graph.alerts.poll()
        graph.alerts.poll()

        assertEquals(
            listOf(
                Triple(AlertKind.CAMPAIGN_STARTED, 1, "Loyal customers"),
                Triple(AlertKind.CAMPAIGN_ENDING, 1, "Loyal customers")
            ),
            notifier.posted
        )
        assertTrue(requests().all { it.requestUrl?.queryParameter("store_id") == "3" })
    }

    @Test
    fun aSupervisorIsToldOnceOfEachNewIncidentFromTheServersWatermark() = runBlocking {
        signIn("COMMERCIAL_SUPERVISOR")
        incidents = page("capture_incidents", listOf(incident("i-1", "2026-11-05T14:02:41Z")))
        graph.alerts.poll()
        assertEquals("the baseline posts nothing", emptyList<Any>(), notifier.posted)
        requests()

        incidents =
            page(
                "capture_incidents",
                listOf(incident("i-2", "2026-11-05T15:00:00Z"), incident("i-1", "2026-11-05T14:02:41Z"))
            )
        graph.alerts.poll()
        graph.alerts.poll()

        assertEquals(listOf(Triple(AlertKind.CAPTURE_INCIDENT, 1, "i-2")), notifier.posted)
        val read = requests().first { it.path.orEmpty().contains("capture-incidents") }
        assertEquals("OPEN", read.requestUrl?.queryParameter("status"))
        assertEquals(
            "the largest server recorded_at minus five minutes, not the device clock",
            "2026-11-05T13:57:41Z",
            read.requestUrl?.queryParameter("recorded_since")?.let { Instant.parse(it).toString() }
        )
        assertEquals(listOf("i-2", "i-1"), graph.alerts.inbox.value.incidents.map { it.incidentId })
    }

    @Test
    fun aSessionThatEndedStopsThePollAndSaysSoOnce() = runBlocking {
        signIn("SALES_ASSOCIATE")
        unauthorized = true

        assertEquals(PollOutcome.SESSION_ENDED, graph.alerts.poll())
        assertEquals(1, cancelled)
        assertNull("the refresh was refused for good", graph.tokens.read())
        assertEquals(listOf(Triple(AlertKind.SESSION_ENDED, 1, null as String?)), notifier.posted)
        // Signed out now: a later poll asks nothing and says nothing more.
        assertEquals(PollOutcome.SIGNED_OUT, graph.alerts.poll())
        assertEquals(1, notifier.posted.size)
    }

    @Test
    fun anUnavailableServiceIsRetriedAndPostsNothing() {
        signIn("COMMERCIAL_SUPERVISOR")
        unavailable = true
        val result = runBlocking { TestListenableWorkerBuilder<NotificationPollWorker>(app).build().doWork() }

        assertEquals(ListenableWorker.Result.retry(), result)
        assertEquals(emptyList<Any>(), notifier.posted)
        assertEquals(0, cancelled)
    }

    @Test
    fun aRefusedWriteIsToldOnceAndReportedOnceAsACaptureIncident() = runBlocking {
        signIn("SALES_ASSOCIATE")
        val saleId = graph.writes.queueSale("c-1", 3, 1, emptyList())
        val drain = suspend {
            OutboxWorker.drain(
                graph.outbox,
                graph.session,
                graph.tokens,
                graph.network.client,
                graph.network.gateway,
                graph.inFlight,
                graph.alerts::refused
            )
        }
        drain()
        drain()

        val sale = graph.outbox.find(saleId)!!
        assertEquals(RowState.REFUSED, sale.state)
        assertEquals(listOf(Triple(AlertKind.CAPTURE_ERROR, 1, null as String?)), notifier.posted)
        // Refusing the same row again (a second device, a replay) tells and reports nothing more.
        graph.alerts.refused(sale)
        assertEquals(1, notifier.posted.size)

        val reports = requests().filter { it.path.orEmpty().endsWith("/capture-incidents") }
        assertEquals("one report, sent once", 1, reports.size)
        val body = Json.parseToJsonElement(reports.single().body.readUtf8()).jsonObject
        assertEquals(
            CaptureIncidents.clientIncidentId(sale.clientId),
            body["client_incident_id"]?.jsonPrimitive?.content
        )
        assertEquals("SALE", body["source_type"]?.jsonPrimitive?.content)
        assertEquals(sale.clientId, body["source_operation_id"]?.jsonPrimitive?.content)
        assertEquals("stock_unavailable", body["refused_code"]?.jsonPrimitive?.content)
        assertEquals("409", body["refused_status"]?.jsonPrimitive?.content)
        assertEquals("3", body["store_id"]?.jsonPrimitive?.content)
        assertTrue("no customer in a report", "customer_id" !in body)
        assertEquals(
            "the report repeats the refused attempt's correlation id",
            sale.correlationId,
            reports.single().getHeader(ApiError.CORRELATION_HEADER)
        )
        val report = graph.outbox.pendingFor("A") + listOfNotNull(graph.outbox.find(saleId + 1))
        assertTrue(report.any { it.operation == Operation.CAPTURE_INCIDENT && it.state == RowState.DONE })
    }

    @Test
    fun signingOutForgetsTheUsersAlertsAndStopsThePoll() = runBlocking {
        signIn("SALES_ASSOCIATE")
        promotions = page("promotions", listOf(promotion(38, 12, "Loyal customers")))
        graph.alerts.poll()
        assertTrue(app.database.notified().subjects("A", AlertKind.CAMPAIGN_STARTED).isNotEmpty())

        graph.sessions.signOutLocally()
        graph.alerts.signedOut("A").join()

        assertTrue(cancelled >= 1)
        assertTrue(notifier.cancelledAll >= 1)
        assertEquals(emptyList<String>(), app.database.notified().subjects("A", AlertKind.CAMPAIGN_STARTED))
        assertEquals(emptyList<Any>(), graph.alerts.inbox.value.campaigns)
    }

    @Test
    fun theClientIncidentIdIsTheUuidV5OfTheRefusedWritesKey() {
        // Python: uuid.uuid5(UUID("69756a29-5973-5fac-8e13-349300b2077d"), "d4f1b3a2-…-0f2e8c3d5a74")
        assertEquals(
            "7798783c-4e5e-5133-a966-24d41d9aa24d",
            CaptureIncidents.clientIncidentId("d4f1b3a2-9c58-4e07-a6b1-0f2e8c3d5a74")
        )
    }

    private companion object {
        const val SALE_REFUSED = """{"code":"stock_unavailable","message":"No stock.","correlation_id":"corr-sale-1"}"""
    }
}
