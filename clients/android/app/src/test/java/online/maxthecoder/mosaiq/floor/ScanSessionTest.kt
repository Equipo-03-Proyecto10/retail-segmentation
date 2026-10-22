package online.maxthecoder.mosaiq.floor

import java.time.Instant
import online.maxthecoder.mosaiq.floor.scan.Payload
import online.maxthecoder.mosaiq.floor.scan.ScanSession
import online.maxthecoder.mosaiq.floor.scan.parseProductId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class ScanSessionTest {
    private val now = Instant.parse("2026-10-21T12:00:00Z")

    @Test
    fun theSameCodeOverManyFramesIsOneScan() {
        val session = ScanSession()
        val first = session.onDecoded("120", now)
        val repeats = (1..30).map { session.onDecoded("120", now.plusMillis(it * 33L)) }

        assertEquals(Payload.ProductId(120), first!!.payload)
        assertEquals(now, first.occurredAt)
        assertEquals(listOf<Any?>(null), repeats.distinct())
    }

    @Test
    fun aDifferentCodeIsANewScanWithItsOwnId() {
        val session = ScanSession()
        val a = session.onDecoded("120", now)!!
        val b = session.onDecoded("245", now)!!
        val c = session.onDecoded("120", now)!!

        assertNotEquals(a.clientEventId, b.clientEventId)
        assertNotEquals(a.clientEventId, c.clientEventId)
    }

    @Test
    fun scanAgainLetsTheSameCodeCountOnceMore() {
        val session = ScanSession()
        assertNotNull(session.onDecoded("120", now))
        assertNull(session.onDecoded("120", now))
        session.scanAgain()
        assertNotNull(session.onDecoded("120", now))
        assertNull(session.onDecoded("120", now))
    }

    @Test
    fun theIdIsFixedWhenTheCodeIsDecoded() {
        var made = 0
        val session = ScanSession(newId = { "id-${++made}" })
        val scan = session.onDecoded("120", now)!!
        session.onDecoded("120", now)
        assertEquals("id-1", scan.clientEventId)
        assertEquals(1, made)
    }

    @Test
    fun onlyAPositiveWholeNumberIsAProductId() {
        assertEquals(Payload.ProductId(120), parseProductId(" 120 "))
        assertEquals(Payload.ProductId(Int.MAX_VALUE), parseProductId("2147483647"))
        for (raw in listOf("", "0", "-1", "abc", "1.5", "+5", "2147483648", "https://x/120", "12 0")) {
            assertEquals(raw, Payload.NotAProduct, parseProductId(raw))
        }
    }
}
