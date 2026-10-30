package online.maxthecoder.mosaiq.floor.ui.components

// ui-string-ok-file: preview (sample copy for design review, never on a screen)

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.ui.theme.MosaiqTheme
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

private const val PREVIEW_WIDTH = 375
private const val PREVIEW_HEIGHT = 900

@Preview(name = "Controls, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun ControlsLight() = Sheet(dark = false) { Controls() }

@Preview(name = "Controls, dark", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT, showBackground = true)
@Composable
private fun ControlsDark() = Sheet(dark = true) { Controls() }

@Preview(name = "Display, light", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT * 2, showBackground = true)
@Composable
private fun DisplayLight() = Sheet(dark = false) { Display() }

@Preview(name = "Display, dark", widthDp = PREVIEW_WIDTH, heightDp = PREVIEW_HEIGHT * 2, showBackground = true)
@Composable
private fun DisplayDark() = Sheet(dark = true) { Display() }

@Composable
private fun Sheet(dark: Boolean, content: @Composable () -> Unit) = MosaiqTheme(darkTheme = dark) {
    Surface(color = MaterialTheme.colorScheme.background) {
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(Spacing.Lg),
            verticalArrangement = Arrangement.spacedBy(Spacing.Lg)
        ) { content() }
    }
}

@Composable
private fun Controls() {
    MqButton("Check in here", {}, fullWidth = true)
    MqButton("Change store", {}, variant = MqButtonVariant.Secondary, fullWidth = true)
    MqButton("Skip", {}, variant = MqButtonVariant.Quiet)
    MqButton("Remove", {}, variant = MqButtonVariant.Danger, leadingIcon = R.drawable.ic_lucide_trash_2)
    MqButton("Saving", {}, loading = true, fullWidth = true)
    MqButton("Disabled", {}, enabled = false, fullWidth = true)
    MqTextField("hello", {}, label = "Store city", supportingText = "Type at least two letters.")
    MqTextField(
        "",
        {},
        label = "Email",
        errorText = "Enter an email address.",
        leading = { MqIcon(R.drawable.ic_lucide_search, contentDescription = null) }
    )
    MqTextField("secret", {}, label = "Password", password = true)
    MqQuantityStepper(value = 2, onDecrease = {}, onIncrease = {})
    MqPager(page = 2, pageCount = 5, onPrevious = {}, onNext = {})
    MqLoadMore(onClick = {})
}

@Composable
private fun Display() {
    Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
        MqBadge("Waiting", tone = MqTone.Neutral, leadingIcon = R.drawable.ic_lucide_clock)
        MqBadge("Selected", tone = MqTone.Accent)
        MqBadge("In stock", tone = MqTone.Success, leadingIcon = R.drawable.ic_lucide_circle_check)
    }
    Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
        MqBadge("Low", tone = MqTone.Warning, leadingIcon = R.drawable.ic_lucide_triangle_alert)
        MqBadge("Out", tone = MqTone.Danger, leadingIcon = R.drawable.ic_lucide_circle_alert)
        MqBadge("Sent", tone = MqTone.Info, leadingIcon = R.drawable.ic_lucide_info)
    }
    MqCard { Text("A card holds one idea.") }
    MqSection("Customer") { Text("Ana Pérez") }
    Column {
        MqListRow("Calle Norte", supporting = "Madrid, ES", onClick = {}, chevron = true, divider = true)
        MqListRow(
            "Waiting to send",
            supporting = "Sale, 14:32",
            trailing = { MqBadge("Waiting", leadingIcon = R.drawable.ic_lucide_clock) }
        )
    }
    MqLoading("Loading stores")
    MqEmptyState(
        icon = R.drawable.ic_lucide_package,
        title = "No stores in that city",
        message = "Check the spelling or try another city.",
        actionLabel = "Clear search",
        onAction = {}
    )
    MqErrorPanel(ApiError.unreachable("abc-123"), onRetry = {})
}
