package online.maxthecoder.mosaiq.floor.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** A surface with a hairline border and the 4dp radius: no shadow, no tint. */
@Composable
fun MqCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.small,
        color = MaterialTheme.colorScheme.surface,
        contentColor = MaterialTheme.colorScheme.onSurface,
        border = BorderStroke(Hairline, MaterialTheme.colorScheme.outlineVariant)
    ) {
        Column(Modifier.padding(Spacing.Lg), verticalArrangement = Arrangement.spacedBy(Spacing.Md), content = content)
    }
}

/** A titled group: the title is a heading for TalkBack, the content sits in a [MqCard]. */
@Composable
fun MqSection(title: String, modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
        Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
        MqCard(content = content)
    }
}

/**
 * "- value +". The buttons are 48dp targets and read "Decrease quantity" and
 * "Increase quantity"; decreasing stops at [min], increasing at [max].
 */
@Composable
fun MqQuantityStepper(
    value: Int,
    onDecrease: () -> Unit,
    onIncrease: () -> Unit,
    modifier: Modifier = Modifier,
    min: Int = 1,
    max: Int = Int.MAX_VALUE
) {
    Row(
        modifier = modifier.border(Hairline, MaterialTheme.colorScheme.outline, MaterialTheme.shapes.small),
        verticalAlignment = Alignment.CenterVertically
    ) {
        IconButton(onClick = onDecrease, modifier = Modifier.size(ControlHeight), enabled = value > min) {
            MqIcon(
                R.drawable.ic_lucide_minus,
                contentDescription = stringResource(R.string.component_decrease_quantity)
            )
        }
        val spoken = stringResource(R.string.a11y_quantity, value)
        Text(
            value.toString(),
            modifier = Modifier.widthIn(min = Spacing.X4l).semantics {
                contentDescription = spoken
                liveRegion = LiveRegionMode.Polite
            },
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.titleMedium
        )
        IconButton(onClick = onIncrease, modifier = Modifier.size(ControlHeight), enabled = value < max) {
            MqIcon(R.drawable.ic_lucide_plus, contentDescription = stringResource(R.string.component_increase_quantity))
        }
    }
}

/** The snackbar host styled by the theme: inverse surface, 4dp radius, the accent action. */
@Composable
fun MqSnackbarHost(hostState: SnackbarHostState, modifier: Modifier = Modifier) {
    SnackbarHost(hostState, modifier) { data ->
        Snackbar(
            snackbarData = data,
            shape = MaterialTheme.shapes.small,
            containerColor = MaterialTheme.colorScheme.inverseSurface,
            contentColor = MaterialTheme.colorScheme.inverseOnSurface,
            actionColor = MaterialTheme.colorScheme.inversePrimary
        )
    }
}
