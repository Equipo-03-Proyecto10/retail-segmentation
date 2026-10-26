package online.maxthecoder.mosaiq.floor.ui.components

import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextAlign
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * A spinner with a label that says what is loading. Inline by default; with
 * [fillArea] it centers itself in the space it is given.
 */
@Composable
fun MqLoading(label: String, modifier: Modifier = Modifier, fillArea: Boolean = false) {
    val body: @Composable () -> Unit = {
        Row(
            modifier = Modifier.semantics(mergeDescendants = true) { liveRegion = LiveRegionMode.Polite },
            horizontalArrangement = Arrangement.spacedBy(Spacing.Md),
            verticalAlignment = Alignment.CenterVertically
        ) {
            CircularProgressIndicator(
                modifier = Modifier.padding(Spacing.Xxs),
                color = MaterialTheme.colorScheme.primary,
                strokeWidth = Spacing.Xxs
            )
            Text(label, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    if (fillArea) {
        Box(modifier.fillMaxSize().padding(Spacing.Lg), contentAlignment = Alignment.Center) { body() }
    } else {
        Box(modifier.padding(vertical = Spacing.Sm)) { body() }
    }
}

/** Nothing to show yet: what is missing, one line of help, and the next step if there is one. */
@Composable
fun MqEmptyState(
    @DrawableRes icon: Int,
    title: String,
    modifier: Modifier = Modifier,
    message: String? = null,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null
) {
    Column(
        modifier = modifier.fillMaxWidth().padding(Spacing.Xxl),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(Spacing.Sm)
    ) {
        MqIcon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant, size = Spacing.X3l)
        Text(
            title,
            style = MaterialTheme.typography.titleMedium,
            textAlign = TextAlign.Center,
            modifier = Modifier.semantics { heading() }
        )
        if (message != null) {
            Text(
                message,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center
            )
        }
        if (actionLabel != null && onAction != null) {
            MqButton(actionLabel, onAction, variant = MqButtonVariant.Secondary)
        }
    }
}

/**
 * A refusal in plain words. The main text is [friendly] when the call site knows
 * better, else the service's own message; the code, the fields and the correlation
 * id sit behind "Details" for a support question. [onRetry] adds a Retry action.
 */
@Composable
fun MqErrorPanel(
    error: ApiError,
    modifier: Modifier = Modifier,
    onRetry: (() -> Unit)? = null,
    friendly: String? = null
) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    val scheme = MaterialTheme.colorScheme
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.small,
        color = scheme.surface,
        border = BorderStroke(Hairline, scheme.error)
    ) {
        Column(Modifier.padding(Spacing.Lg), verticalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
            Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Md), verticalAlignment = Alignment.Top) {
                MqIcon(R.drawable.ic_lucide_circle_alert, contentDescription = null, tint = scheme.error)
                Column(verticalArrangement = Arrangement.spacedBy(Spacing.Xs)) {
                    Text(
                        friendly ?: error.message,
                        style = MaterialTheme.typography.bodyLarge,
                        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }
                    )
                    if (error.code == "future_dated") {
                        Text(
                            "Check the phone's date, time and time zone.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = scheme.onSurfaceVariant
                        )
                    }
                }
            }
            if (onRetry != null) {
                MqButton(
                    "Retry",
                    onRetry,
                    variant = MqButtonVariant.Secondary,
                    leadingIcon = R.drawable.ic_lucide_refresh_cw
                )
            }
            Row(
                modifier =
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = ControlHeight)
                    .clickable(role = Role.Button) { expanded = !expanded }
                    .semantics { stateDescription = if (expanded) "Expanded" else "Collapsed" },
                horizontalArrangement = Arrangement.spacedBy(Spacing.Sm),
                verticalAlignment = Alignment.CenterVertically
            ) {
                MqIcon(
                    if (expanded) R.drawable.ic_lucide_chevron_down else R.drawable.ic_lucide_chevron_right,
                    contentDescription = null,
                    tint = scheme.onSurfaceVariant
                )
                Text("Details", style = MaterialTheme.typography.labelLarge, color = scheme.onSurfaceVariant)
            }
            if (expanded) {
                Column(verticalArrangement = Arrangement.spacedBy(Spacing.Xs)) {
                    val small = MaterialTheme.typography.bodySmall
                    Text("Code: ${error.code}", style = small)
                    error.details.forEach { Text("${it.field}: ${it.code}", style = small) }
                    Text("Correlation ID: ${error.correlationId ?: "none"}", style = small)
                }
            }
        }
    }
}
