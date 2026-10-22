package online.maxthecoder.mosaiq.floor

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.runBlocking
import online.maxthecoder.mosaiq.floor.outbox.FloorDatabase
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class OutboxDaoTest {
    private val database =
        Room
            .inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext(), FloorDatabase::class.java)
            .allowMainThreadQueries()
            .build()
    private val dao = database.outbox()

    @After
    fun close() {
        database.close()
    }

    private fun row(userId: String, clientId: String) = OutboxRow(
        operation = Operation.CHECK_IN,
        path = "api/check-ins/v1/check-ins",
        body = "{}",
        clientId = clientId,
        userId = userId,
        createdAt = 1L
    )

    @Test
    fun pendingRowsAreEachUsersOwnAndInOrder() = runBlocking {
        val first = dao.insert(row("A", "c1"))
        dao.insert(row("B", "c2"))
        val third = dao.insert(row("A", "c3"))
        assertEquals(listOf(first, third), dao.pendingFor("A").map { it.id })
        assertEquals(2, dao.countPendingFor("A"))
        assertEquals(1, dao.countPendingFor("B"))
    }

    @Test
    fun aDoneRowLeavesThePendingListAndKeepsItsAnswer() = runBlocking {
        val id = dao.insert(row("A", "c1"))
        dao.countAttempt(id)
        dao.markDone(id, 201, "{\"reason\":\"no_fix\"}", "corr-1")
        val done = dao.find(id)!!
        assertEquals(RowState.DONE, done.state)
        assertEquals(1, done.attempts)
        assertEquals("{\"reason\":\"no_fix\"}", done.responseBody)
        assertEquals(0, dao.countPendingFor("A"))
    }

    @Test
    fun onlyARefusedRowCanBeDismissed() = runBlocking {
        val pending = dao.insert(row("A", "c1"))
        val refused = dao.insert(row("A", "c2"))
        dao.markRefused(refused, 422, "unknown_store", "No such store.", "corr-2")
        assertEquals(0, dao.dismiss(pending))
        assertEquals(1, dao.dismiss(refused))
        assertNull(dao.find(refused))
        assertEquals(RowState.PENDING, dao.find(pending)!!.state)
    }

    @Test
    fun aFinalAnswerIsNeverOverwritten() = runBlocking {
        val id = dao.insert(row("A", "c1"))
        dao.markRefused(id, 409, "client_check_in_conflict", "Conflict.", null)
        dao.markDone(id, 201, "{}", null)
        assertEquals(RowState.REFUSED, dao.find(id)!!.state)
    }
}
