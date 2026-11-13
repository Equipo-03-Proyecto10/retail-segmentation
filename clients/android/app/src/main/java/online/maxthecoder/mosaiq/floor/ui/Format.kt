package online.maxthecoder.mosaiq.floor.ui

import java.math.BigDecimal
import java.math.RoundingMode
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale
import online.maxthecoder.mosaiq.floor.AppText
import online.maxthecoder.mosaiq.floor.R

private val TimeOfDay: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm", Locale.ENGLISH)
private val DayAndTime: DateTimeFormatter = DateTimeFormatter.ofPattern("d MMM, HH:mm", Locale.ENGLISH)
private val MediumDay: DateTimeFormatter = DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM)
private val DayOnly: DateTimeFormatter = DateTimeFormatter.ofPattern("d MMM yyyy", Locale.ENGLISH)

/** An amount the service sent as text ("12.5"), shown as currency: "$12.50". Unparseable text is kept as is. */
fun formatMoney(amount: String): String = amount.trim().toBigDecimalOrNull()?.let(::formatMoney) ?: "$$amount"

fun formatMoney(amount: BigDecimal): String =
    "$" + String.format(Locale.US, "%,.2f", amount.setScale(2, RoundingMode.HALF_UP))

/** A moment as the clock reads it: "14:32". */
fun formatClock(epochMillis: Long, zone: ZoneId = ZoneId.systemDefault()): String =
    TimeOfDay.format(Instant.ofEpochMilli(epochMillis).atZone(zone))

/** A day in the locale's medium form, for a row older than yesterday. */
fun formatMediumDay(moment: ZonedDateTime): String = MediumDay.format(moment)

/** An enum-like code ("physical_store", "new_customers") in sentence case words. */
fun humanize(code: String): String =
    code.replace('_', ' ').trim().lowercase(Locale.ENGLISH).replaceFirstChar { it.titlecase(Locale.ENGLISH) }

/** "Today, 14:32" for the current day, "12 Mar, 14:32" otherwise. */
fun formatWhen(epochMillis: Long, now: Instant = Instant.now(), zone: ZoneId = ZoneId.systemDefault()): String {
    val moment = Instant.ofEpochMilli(epochMillis).atZone(zone)
    return if (moment.toLocalDate() == now.atZone(zone).toLocalDate()) {
        AppText.get(R.string.outbox_today, TimeOfDay.format(moment))
    } else {
        DayAndTime.format(moment)
    }
}

/** A service timestamp or date as "12 Mar 2025"; text that is neither is returned unchanged. */
fun formatDate(text: String): String {
    val date =
        runCatching { OffsetDateTime.parse(text).toLocalDate() }.getOrNull()
            ?: runCatching { LocalDate.parse(text.take(ISO_DATE_LENGTH)) }.getOrNull()
    return date?.let(DayOnly::format) ?: text
}

private const val ISO_DATE_LENGTH = 10
