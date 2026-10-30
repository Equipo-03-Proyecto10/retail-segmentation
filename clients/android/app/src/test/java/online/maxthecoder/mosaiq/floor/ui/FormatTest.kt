package online.maxthecoder.mosaiq.floor.ui

import java.time.Instant
import java.time.ZoneOffset
import org.junit.Assert.assertEquals
import org.junit.Test

class FormatTest {
    @Test
    fun moneyHasTheSymbolTwoDecimalsAndGrouping() {
        assertEquals("$12.50", formatMoney("12.5"))
        assertEquals("$1,234.00", formatMoney("1234"))
        assertEquals("$0.00", formatMoney("0"))
        assertEquals("\$n/a", formatMoney("n/a"))
    }

    @Test
    fun codesBecomeSentenceCaseWords() {
        assertEquals("Physical store", humanize("physical_store"))
        assertEquals("Loyal regulars", humanize("LOYAL_REGULARS"))
    }

    @Test
    fun timesAreHuman() {
        val now = Instant.parse("2026-03-12T15:00:00Z")
        val sameDay = Instant.parse("2026-03-12T14:32:00Z").toEpochMilli()
        val earlier = Instant.parse("2026-03-10T09:05:00Z").toEpochMilli()
        assertEquals("Today, 14:32", formatWhen(sameDay, now, ZoneOffset.UTC))
        assertEquals("10 Mar, 09:05", formatWhen(earlier, now, ZoneOffset.UTC))
        assertEquals("12 Mar 2026", formatDate("2026-03-12T09:00:00Z"))
        assertEquals("soon", formatDate("soon"))
    }
}
