package online.maxthecoder.mosaiq.floor.ui.theme

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.tooling.preview.Preview

@Preview(name = "Light", widthDp = 375, heightDp = 1100, showBackground = true)
@Composable
private fun ThemePreviewLight() = MosaiqTheme(darkTheme = false) { ThemeSamples() }

@Preview(
    name = "Dark",
    widthDp = 375,
    heightDp = 1100,
    showBackground = true,
    backgroundColor = 0xFF0B0B0A
)
@Composable
private fun ThemePreviewDark() = MosaiqTheme(darkTheme = true) { ThemeSamples() }

@Composable
private fun ThemeSamples() {
    Surface(color = MaterialTheme.colorScheme.background) {
        Column(
            modifier = Modifier.verticalScroll(rememberScrollState()).padding(Spacing.Lg),
            verticalArrangement = Arrangement.spacedBy(Spacing.Lg)
        ) {
            Text("MOSAIQ Floor", style = MaterialTheme.typography.headlineMedium)
            Text("Customer insights", style = MaterialTheme.typography.titleLarge)
            Text(
                "Body text at 14/20 on the canvas, with tabular figures: 1,234.50 and 9,876.25.",
                style = MaterialTheme.typography.bodyMedium
            )
            Text("Secondary text and captions", color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("A link in accent text", color = MaterialTheme.mosaic.link)
            Text("cust-00042 / prod-00137", style = MosaiqIdStyle)

            Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
                Button(onClick = {}) { Text("Primary") }
                OutlinedButton(onClick = {}) { Text("Secondary") }
            }
            OutlinedTextField(value = "Text field", onValueChange = {}, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                value = "Refused",
                onValueChange = {},
                isError = true,
                supportingText = { Text("Something went wrong") },
                modifier = Modifier.fillMaxWidth()
            )

            Row(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
                Badge("Success", MaterialTheme.mosaic.success)
                Badge("Warning", MaterialTheme.mosaic.warning)
                Badge("Info", MaterialTheme.mosaic.info)
                Badge("Danger", MaterialTheme.colorScheme.error)
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(Spacing.Sm)) {
                MaterialTheme.mosaic.segment.forEachIndexed { i, color ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(Spacing.Md).background(color, MaterialTheme.shapes.extraSmall))
                        Text(" Segment ${i + 1}", style = MaterialTheme.typography.labelSmall)
                    }
                }
            }

            HorizontalDivider()
            RoleRow("background", MaterialTheme.colorScheme.background, MaterialTheme.colorScheme.onBackground)
            RoleRow("surface", MaterialTheme.colorScheme.surface, MaterialTheme.colorScheme.onSurface)
            RoleRow(
                "surfaceVariant",
                MaterialTheme.colorScheme.surfaceVariant,
                MaterialTheme.colorScheme.onSurfaceVariant
            )
            RoleRow("primary", MaterialTheme.colorScheme.primary, MaterialTheme.colorScheme.onPrimary)
            RoleRow(
                "primaryContainer",
                MaterialTheme.colorScheme.primaryContainer,
                MaterialTheme.colorScheme.onPrimaryContainer
            )
            RoleRow(
                "secondaryContainer",
                MaterialTheme.colorScheme.secondaryContainer,
                MaterialTheme.colorScheme.onSecondaryContainer
            )
            RoleRow("error", MaterialTheme.colorScheme.error, MaterialTheme.colorScheme.onError)
            RoleRow(
                "errorContainer",
                MaterialTheme.colorScheme.errorContainer,
                MaterialTheme.colorScheme.onErrorContainer
            )
        }
    }
}

@Composable
private fun Badge(label: String, color: Color) {
    // The label carries the meaning; the color only reinforces it.
    Surface(
        shape = MaterialTheme.shapes.extraSmall,
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(Spacing.Xxs, color)
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.padding(horizontal = Spacing.Sm, vertical = Spacing.Xs)
        )
    }
}

@Composable
private fun RoleRow(name: String, container: Color, content: Color) {
    Surface(
        color = container,
        contentColor = content,
        shape = MaterialTheme.shapes.small,
        border = BorderStroke(Spacing.Xxs / 2, MaterialTheme.colorScheme.outline),
        modifier = Modifier.fillMaxWidth().widthIn(min = Spacing.X6l)
    ) {
        Text(name, style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(Spacing.Md))
    }
}
