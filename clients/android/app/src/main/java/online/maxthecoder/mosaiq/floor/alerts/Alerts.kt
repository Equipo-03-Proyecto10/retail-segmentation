package online.maxthecoder.mosaiq.floor.alerts

import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.temporal.ChronoUnit
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import online.maxthecoder.mosaiq.floor.auth.FloorAccess
import online.maxthecoder.mosaiq.floor.auth.TokenStore
import online.maxthecoder.mosaiq.floor.net.Answer
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.CaptureIncident
import online.maxthecoder.mosaiq.floor.net.Network
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.net.TokenAuthenticator
import online.maxthecoder.mosaiq.floor.net.call
import online.maxthecoder.mosaiq.floor.net.totalPages
import online.maxthecoder.mosaiq.floor.outbox.FloorWrites
import online.maxthecoder.mosaiq.floor.outbox.Operation
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.session.SessionStore

/** What one poll came to, which the worker turns into its result. */
enum class PollOutcome {
    /** Read, compared and notified; or nothing to do. */
    DONE,

    /** No answer, a 503 or a rate limit: try again with backoff, having posted nothing. */
    RETRY,

    /** The session is over: one "Sign in" alert was posted and the poll was cancelled. */
    SESSION_ENDED,

    /** Nobody is signed in on this phone. */
    SIGNED_OUT
}

/** An active campaign, as the poll read it: its first promotion stands for it. */
data class CampaignAlert(
    val campaignId: Int,
    val promotion: Promotion,
    val endingSoon: Boolean
)

/** What the updates screen shows: the same facts the notifications carry, from the same pull. */
data class Inbox(
    val campaigns: List<CampaignAlert> = emptyList(),
    val incidents: List<CaptureIncident> = emptyList(),
    val readsIncidents: Boolean = false,
    val campaignsError: ApiError? = null,
    val incidentsError: ApiError? = null,
    val polledAt: Instant? = null
)

/** Where a tapped notification asked the app to open. */
data class AlertTarget(
    val kind: AlertKind,
    val subjectId: String?
)

/**
 * ADR-0071 on the phone: the periodic pull, what counts as new, the capture-error alert and its incident
 * report, and the cleanup at sign-out. Every alert is recorded in `notified_item` before it is posted, by an
 * insert that does nothing when the row exists, so a campaign, an incident or a refused write notifies once.
 */
class Alerts(
    private val network: Network,
    private val tokens: TokenStore,
    private val session: SessionStore,
    private val items: NotifiedItemDao,
    private val writes: FloorWrites,
    private val notifier: Notifier,
    private val schedulePoll: () -> Unit,
    private val cancelPoll: () -> Unit,
    private val clock: () -> Instant = Instant::now,
    private val zone: () -> ZoneId = ZoneId::systemDefault
) {
    // A background pull or cleanup that fails (the database closed, say) must never take the app down; the next
    // pull or sign-in does the work again.
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO + CoroutineExceptionHandler { _, _ -> })
    private val pollLock = Mutex()
    private val current = MutableStateFlow(Inbox())

    /** The last pull, for the updates screen. */
    val inbox: StateFlow<Inbox> = current

    /** A notification the user tapped, until the shell has opened the updates screen for it. */
    val opened = MutableStateFlow<AlertTarget?>(null)

    val notificationsEnabled: Boolean get() = notifier.enabled()

    fun unread(userId: String) = items.observeUnread(userId)

    /**
     * Signed in: the periodic poll is scheduled (KEEP, so it never restarts its clock), the last session's
     * "Sign in" alert is withdrawn, and the foreground poll runs at once.
     */
    fun start(): Job {
        schedulePoll()
        return scope.launch {
            val userId = session.userId.value ?: return@launch
            items.deleteKind(userId, AlertKind.SESSION_ENDED)
            notifier.cancel(AlertKind.SESSION_ENDED)
            poll()
        }
    }

    /** Sign-out: no more polls, no alert left on the phone, and nothing remembered for this user. */
    fun signedOut(userId: String?): Job {
        cancelPoll()
        notifier.cancelAll()
        current.value = Inbox()
        return scope.launch { userId?.let { items.deleteFor(it) } }
    }

    /** The updates screen was seen: its alerts are read and withdrawn. Returns what was unread before. */
    suspend fun markRead(): Set<Pair<AlertKind, String>> {
        val userId = session.userId.value ?: return emptySet()
        val unread = items.unread(userId).map { it.kind to it.subjectId }.toSet()
        items.markRead(userId, clock().toEpochMilli())
        unread.forEach { (kind, subject) -> if (kind != AlertKind.SESSION_ENDED) notifier.cancel(kind, subject) }
        notifier.cancel(AlertKind.CAPTURE_INCIDENT)
        notifier.cancel(AlertKind.CAPTURE_ERROR)
        return unread
    }

    /**
     * A queued write was finally refused (from the outbox worker, ADR-0071): the user is told at once, once
     * per row, and the refusal is queued as one capture-incident report. A refused report is neither.
     */
    suspend fun refused(row: OutboxRow) {
        if (row.operation == Operation.CAPTURE_INCIDENT) return
        if (FloorAccess.runs(session.profile()?.role, FloorAccess.INCIDENT_REPORT)) writes.queueCaptureIncident(row)
        val fresh = items.insert(
            NotifiedItem(row.userId, AlertKind.CAPTURE_ERROR, row.clientId, clock().toEpochMilli())
        )
        if (fresh == -1L) return
        notifier.post(AlertKind.CAPTURE_ERROR, items.countUnread(row.userId, AlertKind.CAPTURE_ERROR))
    }

    /** One pull of every source the signed-in role reads. One at a time: the worker and the screen share it. */
    suspend fun poll(): PollOutcome = pollLock.withLock {
        val userId = session.userId.value
        val role = session.profile()?.role
        if (userId == null || role == null || tokens.read() == null) return@withLock PollOutcome.SIGNED_OUT
        var inbox = current.value.copy(readsIncidents = FloorAccess.runs(role, FloorAccess.INCIDENTS_POLL))
        if (FloorAccess.runs(role, FloorAccess.PROMOTIONS_POLL)) {
            when (val read = readPromotions()) {
                is Answer.Body -> inbox = inbox.copy(campaigns = campaigns(userId, read.value), campaignsError = null)
                is Answer.Refused ->
                    stop(read.error)?.let { return@withLock it }
                        ?: run { inbox = inbox.copy(campaignsError = read.error) }
            }
        }
        if (inbox.readsIncidents) {
            when (val read = readIncidents(userId)) {
                is Answer.Body -> inbox = inbox.copy(incidents = incidents(userId, read.value), incidentsError = null)
                is Answer.Refused ->
                    stop(read.error)?.let { return@withLock it }
                        ?: run { inbox = inbox.copy(incidentsError = read.error) }
            }
        }
        current.value = inbox.copy(polledAt = clock())
        PollOutcome.DONE
    }

    /**
     * What a refusal means for the poll, or `null` when it is only this source's problem (a 403 or a 422,
     * shown on the updates screen). A 401 after the app's single-flight refresh has given up ends the poll;
     * a 401 whose refresh could not be answered (the network or a 503) is retried, like a 503.
     */
    private suspend fun stop(error: ApiError): PollOutcome? = when {
        error.status == 0 || error.status in RESEND -> PollOutcome.RETRY
        error.status == 401 && tokens.read() != null && transient(network.authenticator.lastFailure) ->
            PollOutcome.RETRY
        error.status == 401 -> sessionEnded()
        else -> null
    }

    private fun transient(failure: ApiError?): Boolean = failure != null &&
        failure.code !in TokenAuthenticator.TERMINAL &&
        (failure.status == 0 || failure.status in RESEND)

    private suspend fun sessionEnded(): PollOutcome {
        cancelPoll()
        val userId = session.userId.value
        val fresh =
            userId == null ||
                items.insert(NotifiedItem(userId, AlertKind.SESSION_ENDED, SESSION, clock().toEpochMilli())) != -1L
        if (fresh) notifier.post(AlertKind.SESSION_ENDED, 1)
        return PollOutcome.SESSION_ENDED
    }

    private suspend fun readPromotions(): Answer<List<Promotion>> {
        val storeId = session.chosenStore()?.storeId
        val found = mutableListOf<Promotion>()
        var page = 1
        while (true) {
            when (val answer = call { network.campaigns.listActivePromotions(storeId, page = page) }) {
                is Answer.Refused -> return answer
                is Answer.Body -> {
                    found += answer.value.promotions
                    val pages = totalPages(answer.value.pageInfo.total, answer.value.pageInfo.size)
                    if (page >= pages || page >= MAX_PAGES) return Answer.Body(found)
                }
            }
            page++
        }
    }

    /**
     * Open incidents recorded since the largest server `recorded_at` held, minus five minutes; never the
     * device clock. While some are unread it reads from the oldest unread one, so the updates screen can
     * show them again after a restart.
     */
    private suspend fun readIncidents(userId: String): Answer<List<CaptureIncident>> {
        val mark = items.oldestUnreadIncidentAt(userId) ?: items.newestIncidentAt(userId)
        val since = mark?.let { Instant.ofEpochMilli(it).minus(WATERMARK_SLACK_MINUTES, ChronoUnit.MINUTES).toString() }
        val found = mutableListOf<CaptureIncident>()
        var page = 1
        while (true) {
            when (val answer = call { network.engagement.listCaptureIncidents(recordedSince = since, page = page) }) {
                is Answer.Refused -> return answer
                is Answer.Body -> {
                    found += answer.value.captureIncidents
                    val pages = totalPages(answer.value.pageInfo.total, answer.value.pageInfo.size)
                    if (page >= pages || page >= MAX_PAGES) return Answer.Body(found)
                }
            }
            page++
        }
    }

    /** Compares the active campaigns with what this user was told; the first pull only records them. */
    private suspend fun campaigns(userId: String, promotions: List<Promotion>): List<CampaignAlert> {
        val today = clock().atZone(zone()).toLocalDate()
        val active =
            promotions
                .filter { it.isActive }
                .groupBy { it.campaignId }
                .map { (campaignId, offers) ->
                    val first = offers.minBy { it.promotionId }
                    CampaignAlert(campaignId, first, offers.any { endsSoon(it, today) })
                }.sortedBy { it.campaignId }
        val now = clock().toEpochMilli()
        val baseline = items.insert(NotifiedItem(userId, AlertKind.BASELINE, BASELINE_CAMPAIGNS, now, now)) != -1L
        active.forEach { campaign ->
            val subject = campaign.campaignId.toString()
            val read = if (baseline) now else null
            val started = items.insert(NotifiedItem(userId, AlertKind.CAMPAIGN_STARTED, subject, now, read)) != -1L
            val ending =
                campaign.endingSoon &&
                    items.insert(
                        // A campaign that starts and ends soon in the same pull is told once, as started.
                        NotifiedItem(userId, AlertKind.CAMPAIGN_ENDING, subject, now, if (started) now else read)
                    ) != -1L
            if (baseline) return@forEach
            if (started) notifier.post(AlertKind.CAMPAIGN_STARTED, 1, campaign.promotion.name, subject)
            if (ending && !started) notifier.post(AlertKind.CAMPAIGN_ENDING, 1, campaign.promotion.name, subject)
        }
        return active
    }

    /** Records each open incident by id; new ones after the first pull raise one alert with their count. */
    private suspend fun incidents(userId: String, read: List<CaptureIncident>): List<CaptureIncident> {
        val now = clock().toEpochMilli()
        val baseline = items.insert(NotifiedItem(userId, AlertKind.BASELINE, BASELINE_INCIDENTS, now, now)) != -1L
        val open = read.filter { it.status == "OPEN" }.distinctBy { it.incidentId }
        val arrived =
            open.filter { incident ->
                val at = runCatching {
                    OffsetDateTime.parse(
                        incident.recordedAt
                    ).toInstant().toEpochMilli()
                }.getOrNull()
                val read = if (baseline) now else null
                val item = NotifiedItem(userId, AlertKind.CAPTURE_INCIDENT, incident.incidentId, now, read, at)
                items.insert(item) != -1L
            }
        if (!baseline) {
            arrived.maxByOrNull { it.recordedAt }?.let {
                notifier.post(
                    AlertKind.CAPTURE_INCIDENT,
                    items.countUnread(userId, AlertKind.CAPTURE_INCIDENT),
                    subjectId = it.incidentId
                )
            }
        }
        // Kept from earlier pulls until they are no longer open, newest first.
        val known = (open + current.value.incidents.filter { old -> open.none { it.incidentId == old.incidentId } })
        return known.sortedByDescending { it.recordedAt }
    }

    private fun endsSoon(promotion: Promotion, today: LocalDate): Boolean {
        val ends = runCatching { LocalDate.parse(promotion.endsOn) }.getOrNull() ?: return false
        return !ends.isBefore(today) && !ends.isAfter(today.plusDays(ENDING_DAYS))
    }

    companion object {
        /** Not a final answer: a 503 (auth fails closed), a gateway error or a rate limit. */
        private val RESEND = setOf(429, 502, 503, 504)

        /** ADR-0071: a campaign ending within two days of the device's date. */
        const val ENDING_DAYS = 2L

        const val WATERMARK_SLACK_MINUTES = 5L

        /** Pages read per source per poll, at most; one in practice. */
        const val MAX_PAGES = 4

        private const val SESSION = "session"
        private const val BASELINE_CAMPAIGNS = "campaigns"
        private const val BASELINE_INCIDENTS = "incidents"
    }
}
