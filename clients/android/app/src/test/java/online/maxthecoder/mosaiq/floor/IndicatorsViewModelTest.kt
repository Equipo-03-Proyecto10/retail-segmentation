package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import java.io.File
import java.time.Clock
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.util.concurrent.CopyOnWriteArrayList
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.DemandCoverageReport
import online.maxthecoder.mosaiq.floor.net.DemandCoverageRow
import online.maxthecoder.mosaiq.floor.net.KpiMeasure
import online.maxthecoder.mosaiq.floor.net.KpiReport
import online.maxthecoder.mosaiq.floor.net.KpiRow
import online.maxthecoder.mosaiq.floor.session.ChosenStore
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.indicators.IndicatorPeriod
import online.maxthecoder.mosaiq.floor.ui.indicators.IndicatorsState
import online.maxthecoder.mosaiq.floor.ui.indicators.IndicatorsViewModel
import online.maxthecoder.mosaiq.floor.ui.indicators.window
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/** A contract example, read from the repository (Gradle runs unit tests from clients/android/app). */
fun contractExample(service: String, name: String): String =
    File("../../../contracts/$service/examples/$name").readText()

/** analytics-kpis, campaigns and categories, answering with their contracts' own examples. */
class IndicatorServices : Dispatcher() {
    val requests = CopyOnWriteArrayList<RecordedRequest>()

    /** Stores the signed-in manager is not assigned to. */
    val unassigned = setOf("9")

    /** Paths answered 404, as an operation still `planned` is. */
    val planned = mutableSetOf<String>()

    override fun dispatch(request: RecordedRequest): MockResponse {
        requests += request
        val url = request.requestUrl ?: return error(400, "bad_request")
        val path = url.encodedPath
        if (path in planned) return error(404, "not_found")
        if (url.queryParameter("store_id") in unassigned) {
            return ok(
                403,
                contractExample("analytics-kpis", "forbidden-store.json")
            )
        }
        val kpi = "analytics-kpis"
        return when (path) {
            "/api/analytics-kpis/v1/kpi-reports/net-sales" -> ok(
                200,
                contractExample(kpi, "net-sales-store-scoped.json")
            )
            "/api/analytics-kpis/v1/kpi-reports/conversion" -> ok(200, contractExample(kpi, "conversion.json"))
            "/api/analytics-kpis/v1/kpi-reports/spend" -> ok(200, contractExample(kpi, "spend.json"))
            "/api/analytics-kpis/v1/kpi-reports/demand-coverage" -> ok(
                200,
                contractExample(kpi, "demand-coverage.json")
            )
            "/api/analytics-kpis/v1/kpi-reports/definitions" -> ok(200, contractExample(kpi, "kpi-definitions.json"))
            "/api/campaigns/v1/promotions" -> ok(200, contractExample("campaigns", "promotions.json"))
            "/api/categories/v1/categories" ->
                ok(
                    200,
                    """{"categories":[{"category_id":4,"name":"Footwear","parent_category_id":null}],""" +
                        """"page_info":{"page":1,"size":100,"total":1}}"""
                )
            else -> error(404, "not_found")
        }
    }
}

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class IndicatorsViewModelTest {
    private val server = MockWebServer()
    private val services = IndicatorServices()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private val clock = Clock.fixed(Instant.parse("2026-11-17T15:00:00Z"), ZoneOffset.UTC)

    @Before
    fun start() {
        server.dispatcher = services
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString())
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
    }

    @After
    fun stop() {
        server.shutdown()
        app.database.close()
    }

    private fun settle(model: IndicatorsViewModel): IndicatorsState {
        repeat(300) {
            shadowOf(Looper.getMainLooper()).idle()
            if (model.state.value != IndicatorsState.Loading) return model.state.value
            Thread.sleep(10)
        }
        error("the figures never arrived")
    }

    @Test
    fun aPeriodIsWholeUtcDaysEndingYesterday() {
        val today = LocalDate.parse("2026-11-17")
        assertEquals(
            LocalDate.parse("2026-11-10") to LocalDate.parse("2026-11-16"),
            IndicatorPeriod.LAST_7.window(today)
        )
        assertEquals(LocalDate.parse("2026-10-18"), IndicatorPeriod.LAST_30.window(today).first)
        assertEquals(LocalDate.parse("2026-08-19"), IndicatorPeriod.LAST_90.window(today).first)
    }

    @Test
    fun theManagersStoreShowsEveryFigureForTheChosenPeriod() {
        val model = IndicatorsViewModel(graph, clock)
        model.load(ChosenStore(3, "Coyoacan"))
        val shown = (settle(model) as IndicatorsState.Shown).indicators

        val sales = (shown.sales as Answer.Body).value
        assertEquals("net-sales", sales.kpi)
        assertEquals("201875.25", sales.rows.first().measures.first { it.code == "net_amount" }.value)
        assertEquals("conversion", (shown.conversion as Answer.Body).value.kpi)
        assertEquals("spend", (shown.customers as Answer.Body).value.kpi)
        assertEquals(2, (shown.campaigns as Answer.Body).value.promotions.size)
        // Category 7 covers its demand; 4 and 9 do not.
        assertEquals(listOf(4, 9), shown.stockAlerts.map { it.categoryId })
        assertEquals(listOf(48, 21), shown.stockAlerts.map { it.replenishmentUnits })
        assertEquals("Footwear", shown.categoryNames[4])
        assertNotNull(shown.definition("net-sales"))
        assertEquals(7, services.requests.size)
        services.requests.filter { "/kpi-reports/" in it.path!! && "definitions" !in it.path!! }.forEach {
            val url = it.requestUrl!!
            assertEquals("2026-10-18", url.queryParameter("from"))
            assertEquals("2026-11-16", url.queryParameter("to"))
            assertEquals("3", url.queryParameter("store_id"))
        }
        val promotions = services.requests.single { it.path!!.startsWith("/api/campaigns/") }.requestUrl!!
        assertEquals("true", promotions.queryParameter("active"))
        assertEquals("3", promotions.queryParameter("store_id"))
    }

    @Test
    fun withNoStoreChosenTheServicesCoverTheAssignedStores() {
        val model = IndicatorsViewModel(graph, clock)
        model.choose(IndicatorPeriod.LAST_7)
        model.load(null)
        settle(model) as IndicatorsState.Shown

        services.requests.forEach { assertEquals(null, it.requestUrl!!.queryParameter("store_id")) }
        val sales = services.requests.single { it.path!!.contains("net-sales") }.requestUrl!!
        assertEquals("2026-11-10", sales.queryParameter("from"))
    }

    @Test
    fun anotherStoreIsRefusedByTheServiceAndTheAppSaysSo() {
        val model = IndicatorsViewModel(graph, clock)
        model.load(ChosenStore(9, "Not mine"))
        val refused = settle(model) as IndicatorsState.StoreRefused

        assertEquals("forbidden_store", refused.error.code)
        assertEquals(403, refused.error.status)
        assertEquals("corr-kpi-403b", refused.error.correlationId)
    }

    @Test
    fun aReportNotYetServedIsRefusedOnItsOwnAndTheOthersStay() {
        services.planned += "/api/analytics-kpis/v1/kpi-reports/conversion"
        val model = IndicatorsViewModel(graph, clock)
        model.load(ChosenStore(3, "Coyoacan"))
        val shown = (settle(model) as IndicatorsState.Shown).indicators

        assertEquals(404, (shown.conversion as Answer.Refused).error.status)
        assertTrue(shown.sales is Answer.Body)
    }

    @Test
    fun anotherUsersFiguresDoNotStay() {
        val model = IndicatorsViewModel(graph, clock)
        model.load(ChosenStore(3, "Coyoacan"))
        settle(model) as IndicatorsState.Shown

        graph.session.signedIn(Profile("B", "Demo B", "STORE_MANAGER"))
        shadowOf(Looper.getMainLooper()).idle()

        assertEquals(IndicatorsState.Loading, model.state.value)
    }

    @Test
    fun noModelTheScreenReadsHoldsACustomerField() {
        listOf(
            KpiReport::class,
            KpiRow::class,
            KpiMeasure::class,
            DemandCoverageReport::class,
            DemandCoverageRow::class
        )
            .flatMap { type -> type.java.declaredFields.map { "${type.simpleName}.${it.name}" } }
            .forEach { assertFalse(it, it.contains("customer", ignoreCase = true)) }
    }
}
