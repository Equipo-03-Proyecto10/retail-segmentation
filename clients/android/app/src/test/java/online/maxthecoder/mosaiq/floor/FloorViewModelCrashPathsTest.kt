package online.maxthecoder.mosaiq.floor

import android.os.Looper
import androidx.lifecycle.ViewModelStore
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.testing.TestListenableWorkerBuilder
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockWebServer
import online.maxthecoder.mosaiq.floor.auth.Tokens
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.OutboxWorker
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.session.Profile
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/** Bad stored data and unexpected errors end in a message, never a crash or a stuck spinner (UX-14). */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class FloorViewModelCrashPathsTest {
    private val server = MockWebServer()
    private lateinit var app: FloorTestApp
    private lateinit var graph: AppGraph
    private lateinit var model: FloorViewModel
    private val models = ViewModelStore()

    @Before
    fun start() {
        server.start()
        app = ApplicationProvider.getApplicationContext()
        graph = app.build(server.url("/").toString())
        graph.tokens.write(Tokens("token-A", "refresh-A"))
        graph.session.signedIn(Profile("A", "Demo A", "STORE_MANAGER"))
        model = FloorViewModel(graph)
        models.put("floor", model)
    }

    @After
    fun stop() {
        models.clear()
        server.shutdown()
        app.database.close()
    }

    private fun settle(done: () -> Boolean) {
        repeat(400) {
            shadowOf(Looper.getMainLooper()).idle()
            if (done()) return
            Thread.sleep(10)
        }
        error("Operation never finished")
    }

    private fun row(state: RowState, responseBody: String?) = OutboxRow(
        operation = Operation.CHECK_IN,
        path = "api/stores/v1/check-ins",
        body = "{}",
        clientId = "c-1",
        userId = "A",
        createdAt = 0,
        state = state,
        responseBody = responseBody
    )

    @Test
    fun aMalformedStoredCheckInAnswerDoesNotThrow() {
        assertEquals(
            FloorViewModel.UNREADABLE_ANSWER,
            FloorViewModel.resultOf(row(RowState.DONE, "{not json"))
        )
        assertEquals(
            FloorViewModel.UNREADABLE_ANSWER,
            FloorViewModel.resultOf(row(RowState.DONE, """{"check_in_id": 7}"""))
        )
    }

    @Test
    fun noStoredAnswerYetIsStillNothingToShow() {
        assertNull(FloorViewModel.resultOf(null))
        assertNull(FloorViewModel.resultOf(row(RowState.PENDING, "{not json")))
        assertNull(FloorViewModel.resultOf(row(RowState.DONE, null)))
    }

    @Test
    fun aStoreSearchThatFailsWithAnyExceptionClearsLoadingAndShowsTheError() {
        // A 200 whose body is not the contract's JSON fails inside the converter, not as an IOException.
        server.enqueue(ok(200, "this is not json"))
        model.searchStores("Monterrey")
        settle { !model.stores.value.loading }
        assertFalse(model.stores.value.loading)
        assertEquals("unexpected", model.stores.value.error?.code)
    }

    @Test
    fun aRowWhosePathIsNotAUrlIsRefusedNotCrashedOn() = runBlocking {
        val id = graph.outbox.insert(row(RowState.PENDING, null).copy(path = "http://["))
        TestListenableWorkerBuilder<OutboxWorker>(app).build().doWork()
        val stored = graph.outbox.find(id)
        assertNotNull(stored)
        assertEquals(RowState.REFUSED, stored!!.state)
        assertEquals("invalid_path", stored.code)
        assertEquals(0, server.requestCount)
    }
}
