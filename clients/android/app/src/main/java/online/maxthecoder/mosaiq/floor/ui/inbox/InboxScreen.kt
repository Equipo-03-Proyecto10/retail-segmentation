package online.maxthecoder.mosaiq.floor.ui.inbox

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.alerts.AlertKind
import online.maxthecoder.mosaiq.floor.alerts.CampaignAlert
import online.maxthecoder.mosaiq.floor.alerts.Inbox
import online.maxthecoder.mosaiq.floor.net.CaptureIncident
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqCard
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.formatDate
import online.maxthecoder.mosaiq.floor.ui.humanize
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** Where a notification opens, as the shell saves it: `campaign:<id>` or `incident:<id>`. */
fun focusOf(kind: AlertKind, subjectId: String?): String? = when (kind) {
    AlertKind.CAMPAIGN_STARTED, AlertKind.CAMPAIGN_ENDING -> subjectId?.let { "$CAMPAIGN_FOCUS$it" }
    AlertKind.CAPTURE_INCIDENT -> subjectId?.let { "$INCIDENT_FOCUS$it" }
    else -> null
}

private const val CAMPAIGN_FOCUS = "campaign:"
private const val INCIDENT_FOCUS = "incident:"

/** The updates screen (ADR-0071): campaigns, capture incidents and this phone's refused writes. */
@Composable
fun InboxScreen(model: InboxViewModel, focus: String?, onOpenOutbox: () -> Unit) {
    val inbox by model.inbox.collectAsStateWithLifecycle()
    val fresh by model.fresh.collectAsStateWithLifecycle()
    val refused by model.refused.collectAsStateWithLifecycle()
    val refreshing by model.refreshing.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { model.seen() }
    InboxContent(
        inbox = inbox,
        fresh = fresh,
        refused = refused,
        focus = focus,
        refreshing = refreshing,
        notificationsOn = model.notificationsEnabled,
        onRefresh = model::refresh,
        onOpenOutbox = onOpenOutbox
    )
}

/** Stateless, so previews and tests drive every state. */
@Composable
fun InboxContent(
    inbox: Inbox,
    fresh: Set<Pair<AlertKind, String>>,
    refused: Int,
    focus: String?,
    refreshing: Boolean,
    notificationsOn: Boolean,
    onRefresh: () -> Unit,
    onOpenOutbox: () -> Unit
) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).testTag("inbox"),
        verticalArrangement = Arrangement.spacedBy(Spacing.Lg)
    ) {
        Text(
            stringResource(R.string.inbox_title),
            style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.semantics { heading() }
        )
        if (!notificationsOn) {
            Text(
                stringResource(R.string.inbox_notifications_off),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag("inbox-notifications-off")
            )
        }
        MqButton(
            stringResource(if (refreshing) R.string.inbox_checking else R.string.inbox_check_now),
            onRefresh,
            enabled = !refreshing,
            variant = MqButtonVariant.Secondary,
            leadingIcon = R.drawable.ic_lucide_refresh_cw,
            modifier = Modifier.testTag("inbox-refresh")
        )
        if (refused > 0) {
            MqCard(Modifier.testTag("inbox-refused")) {
                Text(
                    pluralStringResource(R.plurals.inbox_refused, refused, refused),
                    style = MaterialTheme.typography.titleMedium
                )
                MqButton(
                    stringResource(R.string.inbox_open_outbox),
                    onOpenOutbox,
                    variant = MqButtonVariant.Quiet,
                    modifier = Modifier.testTag("inbox-open-outbox")
                )
            }
        }
        if (inbox.readsIncidents) Incidents(inbox, fresh, focus)
        Campaigns(inbox, fresh, focus)
    }
}

@Composable
private fun Campaigns(inbox: Inbox, fresh: Set<Pair<AlertKind, String>>, focus: String?) {
    MqCard(Modifier.testTag("inbox-campaigns")) {
        Text(
            stringResource(R.string.inbox_campaigns),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        inbox.campaignsError?.let { MqErrorPanel(it) }
        if (inbox.campaigns.isEmpty() && inbox.campaignsError == null) {
            Text(
                stringResource(if (inbox.polledAt == null) R.string.inbox_not_checked else R.string.inbox_no_campaigns),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        // The campaign a notification opened comes first.
        val focused = focus?.removePrefix(CAMPAIGN_FOCUS)?.takeIf { focus.startsWith(CAMPAIGN_FOCUS) }
        inbox.campaigns.sortedByDescending { it.campaignId.toString() == focused }.forEach { campaign ->
            CampaignRow(campaign, fresh, campaign.campaignId.toString() == focused)
        }
    }
}

@Composable
private fun CampaignRow(campaign: CampaignAlert, fresh: Set<Pair<AlertKind, String>>, focused: Boolean) {
    val subject = campaign.campaignId.toString()
    val promotion = campaign.promotion
    MqListRow(
        headline = stringResource(R.string.promotions_name_and_discount, promotion.name, percent(promotion)),
        supporting =
        stringResource(R.string.promotions_validity, formatDate(promotion.startsOn), formatDate(promotion.endsOn)),
        divider = true,
        horizontalPadding = Spacing.None,
        modifier = Modifier.testTag(if (focused) "inbox-focused" else "inbox-campaign-$subject"),
        belowSupporting = {
            Column(verticalArrangement = Arrangement.spacedBy(Spacing.Xs)) {
                if (AlertKind.CAMPAIGN_STARTED to subject in fresh) {
                    MqBadge(stringResource(R.string.inbox_new), tone = MqTone.Accent)
                }
                if (campaign.endingSoon) MqBadge(stringResource(R.string.inbox_ends_soon), tone = MqTone.Warning)
            }
        }
    )
}

@Composable
private fun Incidents(inbox: Inbox, fresh: Set<Pair<AlertKind, String>>, focus: String?) {
    MqCard(Modifier.testTag("inbox-incidents")) {
        Text(
            stringResource(R.string.inbox_incidents),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        inbox.incidentsError?.let { MqErrorPanel(it) }
        if (inbox.incidents.isEmpty() && inbox.incidentsError == null) {
            Text(
                stringResource(if (inbox.polledAt == null) R.string.inbox_not_checked else R.string.inbox_no_incidents),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        val focused = focus?.removePrefix(INCIDENT_FOCUS)?.takeIf { focus.startsWith(INCIDENT_FOCUS) }
        inbox.incidents.sortedByDescending { it.incidentId == focused }.forEach { IncidentRow(it, fresh) }
    }
}

/** An incident as reported: its store, what was refused and why. Nobody is named. */
@Composable
private fun IncidentRow(incident: CaptureIncident, fresh: Set<Pair<AlertKind, String>>) {
    MqListRow(
        headline = stringResource(R.string.inbox_incident_headline, incident.storeId, humanize(incident.sourceType)),
        supporting = stringResource(
            R.string.inbox_incident_supporting,
            incident.refusedCode,
            formatDate(incident.occurredAt)
        ),
        divider = true,
        horizontalPadding = Spacing.None,
        modifier = Modifier.testTag("inbox-incident-${incident.incidentId}"),
        belowSupporting = {
            if (AlertKind.CAPTURE_INCIDENT to incident.incidentId in fresh) {
                MqBadge(stringResource(R.string.inbox_new), tone = MqTone.Accent)
            }
        }
    )
}

/** "15.00" as "15", "12.50" as "12.5"; text that is no number is kept as sent. */
private fun percent(promotion: Promotion): String =
    promotion.discountPercent.toBigDecimalOrNull()?.stripTrailingZeros()?.toPlainString() ?: promotion.discountPercent

// ui-string-ok-from: previews below (sample copy for design review, never on a screen)

private val previewInbox =
    Inbox(
        campaigns =
        listOf(
            CampaignAlert(
                12,
                Promotion(38, 12, "Loyal customers", "15.00", "2026-11-01", "2026-11-30", listOf(1), listOf(1), true),
                false
            ),
            CampaignAlert(
                14,
                Promotion(41, 14, "Weekend basket", "10.00", "2026-11-10", "2026-11-19", listOf(1), listOf(1), true),
                true
            )
        ),
        incidents =
        listOf(
            CaptureIncident(
                "7c1e5a90-0000-4f68-8a14-9d0e6b2c4f11",
                "SALE",
                3,
                409,
                "stock_unavailable",
                "2026-11-05T14:02:10Z",
                "2026-11-05T14:02:41Z",
                "OPEN"
            )
        ),
        readsIncidents = true,
        polledAt = java.time.Instant.EPOCH
    )

@Composable
private fun InboxPreview(dark: Boolean) = MosaiqTheme(darkTheme = dark) {
    Column(Modifier.padding(Spacing.Lg)) {
        InboxContent(
            inbox = previewInbox,
            fresh = setOf(AlertKind.CAMPAIGN_STARTED to "14"),
            refused = 2,
            focus = "campaign:14",
            refreshing = false,
            notificationsOn = false,
            onRefresh = {},
            onOpenOutbox = {}
        )
    }
}

@Preview(name = "Updates, light", widthDp = 375, heightDp = 900, showBackground = true)
@Composable
private fun InboxLight() = InboxPreview(dark = false)

@Preview(name = "Updates, dark", widthDp = 375, heightDp = 900, showBackground = true)
@Composable
private fun InboxDark() = InboxPreview(dark = true)
