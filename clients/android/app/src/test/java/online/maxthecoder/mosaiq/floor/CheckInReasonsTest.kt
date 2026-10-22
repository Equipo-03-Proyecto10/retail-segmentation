package online.maxthecoder.mosaiq.floor

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.testing.TestListenableWorkerBuilder
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockWebServer
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.outbox.Fix
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** Each of RN-56's five reasons, as the server answers it, in plain words. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class CheckInReasonsTest {
    private val server = MockWebServer()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph

    @Before
    fun start() {
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString())
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn("A")
    }

    @After
    fun stop() {
        server.shutdown()
        app.database.close()
    }

    private fun answered(status: Int, reason: String, distance: String, verified: Boolean): String = runBlocking {
        val id = graph.writes.queueCheckIn(3, Fix(25.6866, -100.3161, 8.0))
        server.enqueue(
            ok(
                status,
                """{"check_in_id":"c-1","client_check_in_id":"x","user_id":"A","store_id":3,""" +
                    """"latitude":25.6866,"longitude":-100.3161,"accuracy_m":8.0,"distance_m":$distance,""" +
                    """"reason":"$reason","auto_verified":$verified,"verified":$verified,""" +
                    """"occurred_at":"2026-10-21T12:00:00Z","recorded_at":"2026-10-21T12:00:01Z"}"""
            )
        )
        TestListenableWorkerBuilder<OutboxWorker>(app).build().doWork()
        FloorViewModel.resultOf(graph.outbox.find(id))!!
    }

    @Test
    fun withinTheGeofenceIsVerified() {
        assertEquals(
            "You're at the store: 42 m from it, inside its geofence. Verified.",
            answered(201, "within_geofence", "42.4", true)
        )
    }

    @Test
    fun outsideTheGeofenceIsNotVerified() {
        assertEquals(
            "You're 812 m from the store, outside its geofence. Not verified.",
            answered(201, "outside_geofence", "812.0", false)
        )
    }

    @Test
    fun lowAccuracySaysSo() {
        assertEquals(
            "Your location was too imprecise to tell (within 8 m). Not verified.",
            answered(201, "low_accuracy", "10.0", false)
        )
    }

    @Test
    fun aStoreWithoutALocationIsAResultNotAnError() {
        assertEquals(
            "This store has no location set yet, so the check-in could not be measured. Not verified.",
            answered(201, "store_location_unknown", "null", false)
        )
    }

    @Test
    fun noFixIsRecordedUnverified() {
        assertEquals(
            "No location was sent, so the check-in could not be measured. Not verified.",
            answered(201, "no_fix", "null", false)
        )
    }

    @Test
    fun aReplayShowsTheOriginalCheckIn() {
        // check-ins answers a replayed client_check_in_id with 200 and the original.
        assertEquals(
            "You're at the store: 42 m from it, inside its geofence. Verified.",
            answered(200, "within_geofence", "42.4", true)
        )
    }
}
