package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.Promotion
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqCard
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** The promotions the selected customer may be offered, each with "Shown to customer" (F27-07). */
@Composable
fun CustomerPromotions(model: PromotionsViewModel) {
    val state by model.state.collectAsStateWithLifecycle()
    PromotionsContent(
        state = state,
        onRetry = { state.customerId?.let(model::load) },
        onRecord = { model.record(it) }
    )
}

@Composable
fun PromotionsContent(
    state: PromotionsState,
    onRetry: () -> Unit,
    onRecord: (Promotion) -> Unit,
    modifier: Modifier = Modifier
) {
    if (state.customerId == null) return
    MqCard(modifier.testTag("promotions")) {
        Text(
            stringResource(R.string.promotions_title),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        val error = state.error
        val armError = state.armError
        when {
            state.note != null -> Text(state.note, style = MaterialTheme.typography.bodyMedium)
            state.loading -> MqLoading(stringResource(R.string.promotions_loading))
            error != null -> MqErrorPanel(error, onRetry = onRetry.takeIf { error.retryable })
            state.promotions.isEmpty() && armError == null ->
                Text(
                    stringResource(R.string.promotions_none),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("promotions-none")
                )
        }
        if (!state.loading && armError != null) {
            MqErrorPanel(
                armError,
                onRetry = onRetry.takeIf { armError.retryable },
                friendly = stringResource(R.string.promotions_arm_unknown)
            )
        }
        if (!state.loading) {
            state.promotions.forEach { PromotionRow(it, it.promotionId in state.recorded, onRecord) }
        }
    }
}

@Composable
private fun PromotionRow(promotion: Promotion, recorded: Boolean, onRecord: (Promotion) -> Unit) {
    MqListRow(
        headline = stringResource(R.string.promotions_name_and_discount, promotion.name, percent(promotion)),
        supporting =
        stringResource(R.string.promotions_validity, formatDate(promotion.startsOn), formatDate(promotion.endsOn)),
        divider = true,
        horizontalPadding = Spacing.None,
        modifier = Modifier.testTag("promotion-${promotion.promotionId}"),
        belowSupporting = {
            if (recorded) {
                MqBadge(
                    stringResource(R.string.promotions_recorded),
                    tone = MqTone.Success,
                    leadingIcon = R.drawable.ic_lucide_circle_check
                )
            } else {
                MqButton(
                    stringResource(R.string.promotions_record),
                    { onRecord(promotion) },
                    modifier = Modifier.testTag("record-${promotion.promotionId}"),
                    variant = MqButtonVariant.Secondary,
                    leadingIcon = R.drawable.ic_lucide_eye
                )
            }
        }
    )
}

/** "15.00" as "15", "12.50" as "12.5"; text that is no number is kept as sent. */
private fun percent(promotion: Promotion): String =
    promotion.discountPercent.toBigDecimalOrNull()?.stripTrailingZeros()?.toPlainString() ?: promotion.discountPercent

// ui-string-ok-from: previews below (sample copy for design review, never on a screen)

private fun previewPromotion(id: Int, name: String) =
    Promotion(id, 12, name, "15.00", "2026-11-01", "2026-11-30", listOf(1), listOf(1), true)

@Composable
private fun PromotionsPreview(dark: Boolean) = MosaiqTheme(darkTheme = dark) {
    Column(Modifier.padding(Spacing.Lg), verticalArrangement = Arrangement.spacedBy(Spacing.Lg)) {
        PromotionsContent(
            PromotionsState(
                customerId = "c1",
                storeId = 1,
                promotions = listOf(previewPromotion(38, "Loyal customers"), previewPromotion(39, "Weekend basket")),
                recorded = setOf(39)
            ),
            onRetry = {},
            onRecord = {}
        )
        PromotionsContent(PromotionsState(customerId = "c1", storeId = 1), onRetry = {}, onRecord = {})
    }
}

@Preview(name = "Promotions, light", widthDp = 375, heightDp = 700, showBackground = true)
@Composable
private fun PromotionsLight() = PromotionsPreview(dark = false)

@Preview(name = "Promotions, dark", widthDp = 375, heightDp = 700, showBackground = true)
@Composable
private fun PromotionsDark() = PromotionsPreview(dark = true)
